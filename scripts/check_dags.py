"""Carrega as DAGs como o Airflow faria e falha se alguma tiver erro de importação.

Roda dentro da imagem do Airflow (CI):
    docker run --rm -v "$PWD:/opt/kb" -w /opt/kb -e AIRFLOW__CORE__LOAD_EXAMPLES=false \
        --entrypoint python kb-airflow scripts/check_dags.py
"""

import sys
from pathlib import Path

try:  # Airflow 3.x moveu o DagBag em versões recentes
    from airflow.dag_processing.dagbag import DagBag
except ImportError:
    from airflow.models.dagbag import DagBag

EXPECTED = {"kb_sync", "kb_simulate_release", "kb_eval", "kb_health_report", "kb_reindex"}

dags_folder = Path(__file__).resolve().parents[1] / "dags"
sys.path.insert(0, str(dags_folder))  # como no Airflow: módulos da pasta de DAGs são importáveis
bag = DagBag(dag_folder=str(dags_folder))

for path, error in bag.import_errors.items():
    print(f"ERRO em {path}:\n{error}")
missing = EXPECTED - set(bag.dag_ids)
if missing:
    print(f"DAGs ausentes: {', '.join(sorted(missing))}")
print(f"DAGs carregadas: {', '.join(sorted(bag.dag_ids))}")
sys.exit(1 if bag.import_errors or missing else 0)
