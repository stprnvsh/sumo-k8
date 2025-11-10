# Contributing to SUMO-K8

## Development Setup

1. **Clone and setup**:
   ```bash
   git clone <repo>
   cd sumo-k8
   ./setup_local.sh --with-k8s
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run locally**:
   ```bash
   export DATABASE_URL="postgresql://$(whoami)@localhost/sumo_k8"
   python app.py
   ```

## Code Style

- Follow PEP 8 for Python code
- Use type hints where possible
- Keep modules focused on single responsibility
- Add docstrings to all functions

## Testing

```bash
# Test API endpoints
./scripts/test_api.sh

# Test with a real SUMO scenario
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer <api_key>" \
  -F "scenario_id=test" \
  -F "sumo_files=@test_networks/ready_to_use/square.zip"
```

## Project Structure

- `src/` - Source modules (auth, jobs, scaling, etc.)
- `docs/` - Documentation
- `scripts/` - Utility scripts
- `test_networks/` - SUMO test scenarios

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture.

## Pull Request Process

1. Create a feature branch
2. Make your changes
3. Test locally
4. Update documentation if needed
5. Submit PR with description

