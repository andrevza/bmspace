FROM python:3.12-alpine

ENV WORK_DIR=workdir \
  HASSIO_DATA_PATH=/data

RUN mkdir -p ${WORK_DIR}
WORKDIR /${WORK_DIR}
COPY requirements.txt .
COPY config.yaml /tmp/config.yaml

# install python libraries
RUN pip3 install -r requirements.txt

# Extract addon manifest version into a tiny runtime file so the app can
# report the packaged version even when Supervisor does not inject it.
RUN python3 - <<'PY2'
import yaml

with open('/tmp/config.yaml', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f) or {}

version = str(data.get('version', 'dev')).strip() or 'dev'

with open('/workdir/addon_version.txt', 'w', encoding='utf-8') as f:
    f.write(version)
PY2
RUN rm -f /tmp/config.yaml

# Copy code
COPY bms.py constants.py run.sh ./
RUN chmod a+x run.sh

CMD [ "sh", "./run.sh" ]
