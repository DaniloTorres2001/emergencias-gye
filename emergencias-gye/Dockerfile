FROM python:3.13-slim

WORKDIR /app

ENV HOME=/app \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN apt-get update && apt-get install -y \
	build-essential \
	curl \
	git \
	&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY src/ ./src/
COPY .streamlit/ ./.streamlit/

RUN pip3 install --upgrade pip && pip3 install -r requirements.txt

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "src/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]