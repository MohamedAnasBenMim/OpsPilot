.PHONY: install test eval lint up down cluster destroy

install:
	pip install -r requirements-dev.txt

test:
	pytest -q

lint:
	ruff check . && ruff format --check .

eval:
	PYTHONPATH=pilot python eval/run_eval.py
	PYTHONPATH=pilot python eval/run_eval.py --scenarios eval/real --out eval/results_real

up:
	docker compose up --build -d

down:
	docker compose down -v

cluster:
	cd infra/terraform/envs/local && terraform init && terraform apply

destroy:
	cd infra/terraform/envs/local && terraform destroy
