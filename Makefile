.PHONY: up down logs test test-local load-test clean

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f api

test:
	docker compose exec api pytest tests/ -v

test-local:
	pytest tests/ -v

load-test:
	k6 run load_tests/k6_load_test.js

smoke-test:
	k6 run load_tests/k6_smoke_test.js

clean:
	docker compose down -v
	rm -rf __pycache__ app/__pycache__ tests/__pycache__ .pytest_cache
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
