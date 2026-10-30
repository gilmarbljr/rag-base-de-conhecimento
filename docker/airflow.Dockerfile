# Imagem do Airflow com o pacote `kb` instalado. As DAGs só chamam funções desse pacote.
ARG AIRFLOW_VERSION=3.3.0
FROM apache/airflow:${AIRFLOW_VERSION}-python3.12
ARG AIRFLOW_VERSION

COPY --chown=airflow:root pyproject.toml README.md /opt/kb/
COPY --chown=airflow:root src /opt/kb/src

# Instalação editável: o código em /opt/kb/src é montado do host, então mudanças no pacote
# valem sem reconstruir a imagem. Fixar a versão do Airflow impede que o pip a altere.
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -e /opt/kb
