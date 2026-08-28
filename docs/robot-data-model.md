# SceneOps Robot Data Model (Design)

> **이 문서는 다른 `docs/*.md`와 성격이 다르다.** `architecture.md`/`data-model.md`/
> `pipeline-lifecycle.md`/`storage-layout.md`는 "현재 코드가 실제로 어떻게 동작하는가"를
> 기록하는 Phase 0 문서인 반면, 이 문서는 **아직 구현되지 않은 로드맵 Phase 4(ROS2 Robot Data
> Source)의 설계**를 로드맵 §10과 기존 코드의 확장 지점(extension point)에 맞춰 미리 정리한
> forward design 문서다. 여기 적힌 내용은 코드가 아니라 계획이며, Phase 4 착수 시 실제 구현
> 경험에 따라 바뀔 수 있다. 관련 결정: [ADR-005](./adr/005-ros2-vs-kafka-boundary.md).

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

## 3. ROS2 Topics & CAN Replay (로드맵 §10.1)

```text
nuScenes CAN → CanReplayNode → ROS2 Topics

/vehicle/odom      (nav_msgs/Odometry)       → position, orientation, velocity
/vehicle/imu       (sensor_msgs/Imu)          → orientation, acceleration
/vehicle/control   (custom)                   → steering, throttle, brake
/vehicle/status    (sensor_msgs/BatteryState) → battery
/mission/status    (custom)                   → mission_id, operation_state
```

Standard message(`nav_msgs`, `sensor_msgs`, `diagnostic_msgs`)를 우선 사용하고, SceneOps 고유
정보(mission 연결, scene 연결)만 custom message로 정의한다 — 로드맵 원칙 그대로.

## 4. rosbag2/MCAP → SceneOps 적재 흐름 (로드맵 §10.3, §10.4)

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

`RosbagAdapter`는 `RawLogAdapter` Protocol을 구현한다:

```python
class RosbagAdapter:
    async def build_raw_log(
        self, *, dataset_id, dataset_version, raw_log_id, version_root_uri, params
    ) -> tuple[RawLogManifest, RawLogFrameIndex, str, str]:
        # 1. rosbag2/MCAP 파일 열기 (mcap 라이브러리)
        # 2. 토픽 discovery → SensorModality 매핑 (camera/lidar/imu 등)
        # 3. 타임스탬프 정렬 → RawSensorFrameManifest 리스트 생성
        # 4. RobotState 관련 토픽(odom/imu/control/status)은 별도로 RobotState 레코드로 추출
        #    (frame_index가 아니라 §2의 RobotState 테이블로)
        # 5. RawLogManifest/RawLogFrameIndex 조립 + ArtifactStore에 기록
        ...
```

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

## 6. Phase 4 착수 시 확인해야 할 것

- `RawLogAdapter.build_raw_log()`가 반환하는 `RawLogFrameIndex`가 카메라/LiDAR처럼 "프레임"
  단위 데이터에 최적화되어 있는데, IMU/odom처럼 훨씬 높은 주파수(수십~수백 Hz)의 시계열을
  같은 구조에 넣는 게 적절한지 — 위 §2 제안대로 `RobotState`를 별도 테이블/Parquet으로 분리하는
  근거가 여기서 나온다.
- `SensorModality`(`packages/sceneops-core/sceneops_core/sensors/enums.py`)에 로봇 상태류를
  담을 값이 없다 (`CAMERA`/`LIDAR`/`RADAR`/`EGO_POSE`/`CALIBRATION`/`ANNOTATION`/`UNKNOWN`뿐) —
  `TELEMETRY` 또는 `ROBOT_STATE` 추가 여부 결정 필요.
- `RawLogSourceFormat.ROSBAG`을 실제로 어디서 쓸지 — `RawLogManifest.source_format`에 채워
  넣는 용도로 그대로 쓰면 될 것으로 보이나 미검증.
