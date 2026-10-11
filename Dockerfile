# Personal Finance: dashboard + importer in one image.
# No personal data is baked in: var/ data/ output/ backup/ members.yaml are
# mounted at run time (see docker-compose.yml and the README).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY personal-finance.py impex.py ui_theme.py ./
COPY .streamlit ./.streamlit
COPY finance ./finance
COPY backend ./backend

# Mount points; owned by the runtime user so a fresh volume is writable.
RUN useradd --uid 1000 --create-home --home-dir /home/app app \
    && mkdir -p var data output backup \
    && chown -R app:app /app
USER app

ENV FINANCE_DB=/app/var/networth.db
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8501/_stcore/health', timeout=3)"

ENTRYPOINT ["streamlit", "run", "personal-finance.py", \
            "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
