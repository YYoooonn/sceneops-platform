# --------------------
# Fixtures
# --------------------

.PHONY: register-nuscenes-dataset
register-nuscenes-dataset:
	chmod +x scripts/fixtures/register_nuscenes_dataset.sh
	API_PREFIX=$(API_PREFIX) scripts/fixtures/register_nuscenes_dataset.sh
