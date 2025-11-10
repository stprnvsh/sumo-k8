# SUMO Test Networks

This directory contains SUMO test networks for testing the SUMO-K8 system.

## Small Test Networks (2-10 vehicles)

- **square.zip** - Simple square network
- **cross.zip** - Cross intersection
- **corridor.zip** - Corridor network
- **emission.zip** - Emission testing (has amitran-output issue)
- **emission_fixed.zip** - Fixed version (recommended)

## Medium Test Networks (100+ vehicles)

- **quickstart.zip** (361KB) - SUMO tutorial quickstart scenario
  - Network: ~300KB
  - Routes: ~300KB
  - Good for testing moderate traffic

- **Doerpfeldstr.zip** (44KB) - Doerpfeldstr scenario
  - Multiple route files
  - Various modes (cars, bikes, public transport)

- **Wildau.zip** (469KB) - Wildau network
  - Large network file (3.2MB uncompressed)
  - Multiple configuration options

- **bologna-acosta.zip** - Bologna Acosta scenario
  - Real-world network
  - Bus lanes included

- **bologna-persontrips.zip** - Bologna with person trips
  - Pedestrian network included
  - More complex scenario

## Usage

```bash
# Test with quickstart (medium size)
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer <api_key>" \
  -F "scenario_id=quickstart_test" \
  -F "cpu_request=2" \
  -F "memory_gi=4" \
  -F "sumo_files=@test_networks/ready_to_use/quickstart.zip"

# Test with larger network
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer <api_key>" \
  -F "scenario_id=wildau_test" \
  -F "cpu_request=4" \
  -F "memory_gi=8" \
  -F "sumo_files=@test_networks/ready_to_use/Wildau.zip"
```

## Downloading More Networks

See [README_LARGE.md](README_LARGE.md) for instructions on downloading even larger networks from:
- SUMO scenarios repository
- OpenStreetMap data
- LuST scenario
