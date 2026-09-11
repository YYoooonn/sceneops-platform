# SceneOps Robot Data Model (Design)

> **이 문서는 다른 `docs/*.md`와 성격이 다르다.** `architecture.md`/`data-model.md`/
> `pipeline-lifecycle.md`/`storage-layout.md`는 "현재 코드가 실제로 어떻게 동작하는가"를
> 기록하는 Phase 0 문서인 반면, 이 문서는 로드맵 Phase 4(ROS2 Robot Data Source)의 설계를
> 로드맵 §10과 기존 코드의 확장 지점(extension point)에 맞춰 정리한 문서다. 관련 결정:
> [ADR-005](./adr/005-ros2-vs-kafka-boundary.md).
>
> **구현 현황 (최초 작성 이후 갱신)**: §2(RobotState 스키마/DB), §3(CanReplayNode),
> §4(RosbagAdapter)는 이제 실제로 구현되어 있다 — 아래 각 절에 실제 코드 경로를 표시해뒀다.
> 실제 nuScenes CAN bus 데이터로 `ros2 bag record`를 돌려 만든 real MCAP까지 이 adapter에
> 통과시켜 로드맵 §10의 "nuScenes CAN replay → ROS2 pub/sub → rosbag2/MCAP → RosbagAdapter
> ingestion" 체인 전체를 실제로 검증했다 (`apps/worker/tests/fixtures/rosbag/can_replay_scene_0061.mcap`).
> 남은 미구현 항목은 §6 참고. 이 아래 나머지 설계 서술은 여전히 유효하지만, "아직 코드가
> 없다"는 전제로 쓰인 문장들은 더 이상 정확하지 않다.

## 1. 왜 생각보다 갭이 작은가

로드맵은 Phase 4를 "SceneOps에 처음으로 robotics runtime data source를 추가"하는 것으로 그리지만,
실제 코드를 조사한 결과 `RAW_LOG_SCENE_BUILDING` 파이프라인(`build_scenes` job)이 이미 로드맵이
제안하는 `DatasetAdapter` 패턴과 거의 동일한 구조로 만들어져 있다:

```text
RawLogAdapter (Protocol, apps/worker/sceneops_worker/observations/adapters/base.py)
  async def build_raw_log(...) -> (RawLogManifest, RawLogFrameIndex, manifest_uri, frame_index_uri)

RawLogAdapterFactory (.../observations/adapters/factory.py)
  register(source_type: RawLogSourceType, adapter: RawLogAdapter)
  get(source_type) -> RawLogAdapter

현재 등록된 adapter: NuScenesRawLogMocker (source_type=NUSCENES_RAW_LOG_MOCK)
```

그리고 스키마 레벨(`packages/sceneops-core/sceneops_core/observations/schemas/enums.py`)에는
이미 로봇 데이터를 겨냥한 값들이 **미사용 placeholder로 존재**한다:

```python
RawLogSourceFormat.ROSBAG          # 정의만 되어 있고 어디서도 생성되지 않음
RawLogSourceType.REAL_ROBOT_LOG    # 정의만 되어 있고 등록된 adapter 없음
RawLogSourceType.SIMULATOR_LOG     # 정의만 되어 있고 등록된 adapter 없음
```

즉 Phase 4의 핵심 작업은 "새 추상화를 설계하는 것"이 아니라 **이미 있는 `RawLogAdapter`
Protocol을 구현하는 `RosbagAdapter` 클래스 하나를 작성하고 `REAL_ROBOT_LOG`에 등록하는 것**에
가깝다. `RawLogManifest`/`RawLogFrameIndex`(`observations/schemas/raw_logs.py`)도 이미
nuScenes에 종속되지 않은 일반 스키마다 (`channels`, `modalities: list[SensorModality]`,
`frame_count`, `sequence_count`, `time_range`, `ego_poses`, `calibrations`).

이 문서는 그 위에 남은 두 가지 — (a) 로봇 런타임 상태를 만드는 것, (b) rosbag2/MCAP을 그
스키마로 변환하는 것 — 를 설계한다.

## 2. Canonical RobotState (로드맵 §10.2)

`RawLogManifest`/`RawLogFrameIndex`는 "센서 프레임 시퀀스"를 표현하기 위한 스키마이고,
로봇의 **런타임 상태**(위치, 속도, 배터리, 조작 상태)는 여기 들어맞지 않는다. 새 엔티티가
필요하다:

```text
RobotState

robot_id
timestamp_us          # RawSensorFrameManifest와 동일하게 us 단위로 통일
scene_id | raw_log_id  # 어느 scene/raw log에 속하는지 (SceneRecord.parent_scene_id 패턴과 동일하게 옵션)
mission_id

position: list[float]           # RawEgoPoseManifest.translation과 동일 형태
orientation: list[float]        # RawEgoPoseManifest.rotation (quaternion_wxyz 기본값도 동일하게 맞춤)

velocity: list[float] | None
acceleration: list[float] | None

steering: float | None
throttle: float | None
brake: float | None

battery: float | None
operation_state: str            # enum화 예정 (IDLE/RUNNING/ERROR/E_STOP 등)

metadata: JsonDict              # RawEgoPoseManifest.metadata와 동일 패턴
```

기존 `RawEgoPoseManifest`(`observations/schemas/frames.py`)와 필드 이름·타입을 의도적으로
맞췄다 — ego_pose는 이미 "센서와 무관한 로봇 위치/자세" 개념이라 `RobotState`의 부분집합에
가깝다. `RobotState`를 `RawEgoPoseManifest`의 상위 확장으로 볼지, 완전히 별도 엔티티로 둘지는
Phase 4 착수 시 실제 rosbag 데이터로 검증 후 결정한다.

## 3. ROS2 Topics & CAN Replay (로드맵 §10.1) — 구현됨: `ros2/nodes/can_replay_node.py`

```text
nuScenes CAN (data/raw/nuscenes/can_bus/) → CanReplayNode → ROS2 Topics

/vehicle/odom      (nav_msgs/Odometry)       ← CAN 'pose'             → position, orientation, velocity
/vehicle/imu       (sensor_msgs/Imu)          ← CAN 'ms_imu'           → orientation, acceleration
/vehicle/control   (std_msgs/String, JSON)    ← CAN 'vehicle_monitor'  → steering, throttle, brake
/vehicle/status    (sensor_msgs/BatteryState) ← CAN 'vehicle_monitor'  → battery
/mission/status    (std_msgs/String, JSON)    ← synthetic (replay start/end) → Mission 엔티티 몫, RobotState 아님
```

Standard message(`nav_msgs`, `sensor_msgs`)를 우선 사용한다는 로드맵 원칙대로 구현했다.
`/vehicle/control`은 steering+throttle+brake 조합에 맞는 표준 ROS2 메시지가 없고, 커스텀
`.msg` 패키지를 만들려면 colcon build 단계가 필요해 이 replay node 범위 밖으로 미뤘다 —
대신 `std_msgs/String`에 flat JSON을 실어 보내는 브릿지 포맷을 쓴다 (`RosbagAdapter`가
`std_msgs/msg/String` 스키마를 인식해서 `.data`를 다시 JSON으로 파싱하도록 되어 있다 —
§4 참고).

**중요한 정정**: 원래 이 표는 `/mission/status`가 `RobotState.operation_state`를 채운다고
적었지만 실제로 구현하면서 깨졌다 — CanReplayNode가 보내는 `operation_state` 값
("running"/"completed")은 `MissionStatus` 값이지 `RobotOperationState`
(idle/running/error/emergency_stop) 값이 아니라서, `RobotStateRecord`에 그대로 넣으면
pydantic validation이 "completed"를 거부한다. 그래서 `RosbagAdapter`는 `/mission/status`를
`extract_robot_states()` 대상 토픽에서 의도적으로 제외했다 — 이 토픽은 §5의 `Mission`
엔티티가 담당해야 할 몫이고, `RosbagAdapter.extract_missions()`가 그 소비 경로다: 같은
`mission_id`로 온 여러 상태 업데이트(시작/종료)를 하나의 `MissionRecord`로 합치고,
`operation_state` 문자열을 `MissionStatus` enum으로 매핑한다 (모르는 값은 `PENDING`으로
안전하게 fallback). `IngestRobotStatesJobHandler`가 `extract_robot_states()`와 같은 pass에서
호출해 `RobotStore.upsert_mission()`으로 저장한다 — 같은 bag을 두 번 열 필요가 없어서 별도
Job으로 안 만들었다.

nuScenes CAN bus 쿼터니언은 `(w, x, y, z)` 순서이고(같은 scene의 `ego_pose['rotation']`과
수치 비교로 확인), ROS2 `geometry_msgs/Quaternion`은 `(x, y, z, w)` 순서라 재정렬이 필요하다
— `can_replay_node.py`의 `_quat_wxyz_to_ros()`.

## 4. rosbag2/MCAP → SceneOps 적재 흐름 (로드맵 §10.3, §10.4) — 구현됨:
`apps/worker/sceneops_worker/datasets/ingestion/rosbag_raw_log.py`

```text
ROS2 Topics
     ↓ rosbag2 record
rosbag2 / MCAP 파일
     ↓ RosbagAdapter.build_raw_log()          [신규 구현]
RawLogManifest + RawLogFrameIndex              [기존 스키마 재사용]
     ↓ SceneBuilder.build()                    [기존 코드 재사용, build_scenes.py]
SceneRecord (scene_manifest_uri, lineage)      [기존 테이블 재사용]
     ↓
RobotState 시계열                              [신규 테이블, §2]
     └ scene_id로 SceneRecord와 연결
```

`RosbagAdapter`는 `RawLogAdapter` Protocol을 구현한다. 실제 구현은 이 문서가 처음 예상한
것보다 인코딩 처리가 하나 더 필요했다 — 실제 ROS2 bag은 CDR로 인코딩되기 때문에, `mcap`
라이브러리로 채널/메시지를 열람하는 것 외에 `mcap-ros2-support`(`rclpy` 불필요, MCAP 파일에
내장된 스키마 텍스트만으로 디코딩)로 CDR 페이로드를 실제 dict로 바꾸는 단계가 필요했다:

```python
class RosbagAdapter:
    async def build_raw_log(
        self, *, dataset_id, dataset_version, raw_log_id, version_root_uri, params
    ) -> tuple[RawLogManifest, RawLogFrameIndex, str, str]:
        # 1. rosbag2/MCAP 파일 열기 (mcap.reader.make_reader)
        # 2. 메시지 인코딩별 디코딩:
        #    - cdr: mcap_ros2.decoder.DecoderFactory로 실제 ROS2 메시지 디코딩,
        #      __slots__ 재귀 순회로 일반 dict 변환. nav_msgs/Odometry,
        #      sensor_msgs/Imu, sensor_msgs/BatteryState는 flat 필드로 재매핑;
        #      std_msgs/String은 .data를 다시 JSON 파싱 (§3의 브릿지 포맷)
        #    - json: 테스트 픽스처용 브릿지 포맷, 그대로 dict
        # 3. 토픽 discovery → SensorModality 매핑 (camera/lidar 등)
        # 4. 타임스탬프 정렬 → RawSensorFrameManifest 리스트 생성
        # 5. RobotState 관련 토픽(odom/imu/control/status)은 별도로 RobotState 레코드로 추출
        #    (frame_index가 아니라 §2의 RobotState 테이블로) — extract_robot_states()
        # 6. RawLogManifest/RawLogFrameIndex 조립 + ArtifactStore에 기록
        ...
```

미구현: 카메라/LiDAR 같은 바이너리 센서 페이로드(`sensor_msgs/Image`, `PointCloud2`)는 CDR
디코딩까지는 되지만 파일로 안 써서(`RawSensorFrameManifest.uri`가 빈 문자열로 남음) 완전한
scene 등록까지는 못 간다 — 실제 카메라/LiDAR 퍼블리셔가 생기면 처리할 후속 작업.

이후 `build_scenes.py`의 `_build_adapter_factory`에 한 줄 추가로 등록한다:

```python
factory.register(RawLogSourceType.REAL_ROBOT_LOG, RosbagAdapter(...))
```

`BuildScenesJobHandler.run()` 이하 파이프라인(`SceneBuilder`, artifact 등록, dataset version
갱신)은 **코드 변경 없이 그대로 재사용**된다 — adapter가 nuScenes 목업이든 실제 rosbag이든
`RawLogManifest`/`RawLogFrameIndex`만 만들어내면 되기 때문이다. 이는 기존 `ArtifactStore`
추상화([ADR-002](./adr/002-object-storage-for-assets.md))가 스토리지 백엔드 전환을 코드
변경 없이 지원하는 것과 동일한 설계 이득이다.

Object Storage 레이아웃(`storage-layout.md` §3)에는 rosbag/MCAP 원본을 위한 prefix가 아직
없다 — `RawSourceSettings`가 이미 raw dataset을 위한 독립 root(`/data/raw/nuscenes`)를 두는
패턴을 따라 `/data/raw/rosbag/{robot_id}/{run_id}.mcap` 형태를 제안한다.

## 5. 신규 엔티티와 기존 domain의 관계

```text
Robot        robot_id, name, platform 등 정적 메타데이터
RobotRun     robot_id + raw_log_id 1:1 — "이 로봇의 이 실행이 이 raw log로 기록됨"
Mission      mission_id, robot_id, status, RobotState.mission_id가 참조
```

- `RobotRun`은 `data-model.md` §5의 `PipelineRun`과는 다른 개념이다 — `PipelineRun`은 SceneOps
  내부 처리 실행이고, `RobotRun`은 로봇이 실제로 움직인 물리적 실행(rosbag 하나에 대응)이다.
- `SceneRecord.parent_scene_id`/`lineage`(JSONB) 패턴을 그대로 재사용해 "이 scene이 어느
  `RobotRun`에서 나왔는지"를 추적한다 — 새 lineage 메커니즘을 만들지 않는다.
- Quality gate([pipeline-lifecycle.md](./pipeline-lifecycle.md) §4)도 그대로 재사용 가능하다 —
  예를 들어 "CAM_FRONT/LIDAR_TOP 토픽 존재 여부", "센서 timestamp tolerance"는 로드맵 §9가
  요구하는 robotics-specific validation과 정확히 겹치고, `validate_scene` task의
  `PipelineTaskQualityRule` 메커니즘에 새 rule을 추가하는 것만으로 확장된다.

## 6. 남은 gap (구현하면서 확인/정정된 목록)

해결됨:

- ~~`RawLogFrameIndex`에 고주파 시계열을 넣는 게 적절한지~~ — `RobotState`를 별도 테이블로
  분리하는 것으로 확정, `RawLogFrameIndex`는 프레임 데이터 전용으로 유지
- ~~`RawLogSourceFormat.ROSBAG`을 어디서 쓸지~~ — `RawLogManifest.source_format`에 그대로 사용
- ~~`SensorModality`에 로봇 상태류 값이 없는 문제~~ — `RobotState`는 애초에 `RawSensorFrameManifest`/
  `SensorModality`를 전혀 쓰지 않는 별도 스키마라서 해당 없음으로 판명
- ~~`/mission/status` → `Mission` 엔티티 소비 경로 없음~~ — `RosbagAdapter.extract_missions()` +
  `IngestRobotStatesJobHandler`의 `RobotStore.upsert_mission()` 호출로 구현. 같은 `mission_id`의
  여러 상태 업데이트를 하나의 row로 합치는 로직까지 포함 (§3 참고)

아직 남음:

- **바이너리 센서 페이로드 미기록** — §4의 "미구현" 참고. `sensor_msgs/Image`/`PointCloud2`를
  ArtifactStore에 파일로 쓰는 경로가 없음. 실제 카메라/LiDAR 퍼블리셔가 없어서 지금은 검증
  불가 (합성 데이터로 만들 수는 있지만 실효성이 낮다고 판단해 보류)
- **`Robot`/`RobotRun` 사전 등록 흐름 없음** — `IngestRobotStatesJobHandler`는 `Robot`/
  `RobotRun`이 이미 DB에 있다고 가정한다. CAN replay를 실행하기 전에 이 둘을 등록하는 API나
  CLI가 아직 없어서, 지금은 테스트에서 직접 레코드를 만들어 우회하고 있다
- **CAN replay 속도/커스텀 메시지 트레이드오프** — `/vehicle/control`이 정식 `.msg` 패키지가
  아니라 `std_msgs/String` JSON 브릿지인 것은 의도적 스코프 축소였다 (colcon build 없이 가는
  선택) — 나중에 실물 로봇 연동이 필요해지면 재검토
- **커밋된 테스트 픽스처가 실물 데이터** — `apps/worker/tests/fixtures/rosbag/can_replay_scene_0061.mcap`은
  손으로 만든 바이트가 아니라 `ros2 bag record`가 실제로 만든 파일 (1.4MB) — 이후 CAN
  replay 로직이 바뀌면 이 픽스처를 재생성해야 값이 안 맞을 수 있음
