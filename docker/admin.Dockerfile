# Painel de administração (Streamlit) com o pacote `kb` instalado.
FROM python:3.12-slim

WORKDIR /opt/kb
COPY pyproject.toml README.md /opt/kb/
COPY src /opt/kb/src

# Instalação editável: src/ e admin/ são montados do host.
RUN pip install --no-cache-dir -e ".[admin]"

EXPOSE 8501
CMD ["streamlit", "run", "admin/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
