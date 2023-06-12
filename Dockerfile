FROM python:3.11-alpine

WORKDIR /usr/src/teslamate-abrp

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY teslamate_mqtt2abrp.py .

# Create a non-root user
RUN adduser -D toor
USER toor

ENTRYPOINT [ "python", "-u", "teslamate_mqtt2abrp.py" ]
