from datetime import datetime, timezone
from fractions import Fraction
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import boto3
from botocore.config import Config


API = "/2017-08-29/jobs"
MODES = {"complete", "lost-response", "corrupt-output", "complete-without-output", "wait"}
REGION = "us-east-1"
ACCOUNT = "123456789012"
INPUT_KEY = re.compile(r"submissions/transcode-input/[1-9][0-9]*/[a-f0-9]{32}/[^/]+")
OUTPUT_STEM = re.compile(r"submissions/transcode-output/[1-9][0-9]*/[a-f0-9]{32}/playback")


class ProviderFixture:
    def __init__(self):
        endpoint = os.environ["SCRAPBOOK_FIXTURE_ENDPOINT"]
        self.bucket = os.environ["SCRAPBOOK_FIXTURE_BUCKET"]
        self.queue = os.environ["SCRAPBOOK_FIXTURE_RESULT_QUEUE"]
        self.job_queue = os.environ["SCRAPBOOK_FIXTURE_JOB_QUEUE"]
        self.role = os.environ["SCRAPBOOK_FIXTURE_JOB_ROLE"]
        if not re.fullmatch(r"http://scrapbook-f08-media-[a-f0-9]{16}:5000", endpoint) or not self.queue.startswith(endpoint + "/"):
            raise ValueError("A fixture requer um endpoint interno do Moto descartável.")
        if not re.fullmatch(r"scrapbook-f08-media-[a-f0-9]{16}", self.bucket):
            raise ValueError("A fixture requer um bucket descartável da F08.")
        settings = {"endpoint_url": endpoint, "region_name": REGION,
                    "config": Config(s3={"addressing_style": "path"}, retries={"total_max_attempts": 1})}
        self.s3 = boto3.client("s3", **settings)
        self.sqs = boto3.client("sqs", **settings)
        self.audio = Path("/fixture/reference.m4a").read_bytes()
        reference_dir = Path("/fixture/video")
        catalog = reference_dir / "catalog.json"
        self.video_references = {
            (item["width"], item["height"], Fraction(item["rate"]), item["rotation"], item["channels"]):
            (reference_dir / item["file"]).read_bytes()
            for item in json.loads(catalog.read_text(encoding="utf-8"))
        } if catalog.exists() else {}
        self.clock = Path("/fixture/clock.txt")
        self.jobs = {}
        self.requests = {}
        self.mode = "complete"
        self.lock = Lock()

    def private_key(self, uri, allowed):
        address = urlsplit(uri)
        key = address.path.removeprefix("/")
        if address.scheme != "s3" or address.netloc != self.bucket or address.query or address.fragment or not allowed.fullmatch(key):
            raise ValueError("O job usa uma referência fora do prefixo privado.")
        return key

    def receive(self, request):
        token = request["clientRequestToken"]
        attempt = UUID(token)
        self.requests[token] = self.requests.get(token, 0) + 1
        if token in self.jobs:
            return self.jobs[token]["job"], False
        if request["role"] != self.role or request["queue"] != self.job_queue:
            raise ValueError("Fila ou role do job divergente.")
        settings = request["settings"]
        input_settings, = settings["inputs"]
        group, = settings["outputGroups"]
        output, = group["outputs"]
        destination = group["outputGroupSettings"]["fileGroupSettings"]["destination"]
        key = self.private_key(input_settings["fileInput"], INPUT_KEY)
        output_key = self.private_key(destination, OUTPUT_STEM) + "." + output["extension"]
        if key.split("/")[3] != attempt.hex or key.split("/")[2:4] != output_key.split("/")[2:4]:
            raise ValueError("O job usa um prefixo de outro arquivo ou tentativa.")
        if output["containerSettings"]["container"] != "MP4":
            raise ValueError("Contêiner de saída divergente.")
        content_type, reference = self.reference_for(input_settings, output)
        loaded = self.s3.get_object(Bucket=self.bucket, Key=key)
        with loaded["Body"] as body:
            data = body.read()
        if not loaded.get("VersionId") or not data:
            raise ValueError("Entrada versionada ausente.")
        now = datetime.fromisoformat(self.clock.read_text().strip()).astimezone(timezone.utc)
        job_id = f"{int(now.timestamp() * 1000)}-{uuid4().hex[:6]}"
        job = {"id": job_id, "arn": f"arn:aws:mediaconvert:{REGION}:{ACCOUNT}:jobs/{job_id}",
               "status": "PROGRESSING", "queue": self.job_queue, "role": self.role,
               "settings": settings, "userMetadata": request["userMetadata"],
               "createdAt": now.timestamp(), "timing": {"submitTime": now.timestamp()}}
        entry = {"job": job, "output": output_key, "input_sha": sha256(data).hexdigest(),
                 "input_version": loaded["VersionId"], "input_format": self.input_format(data),
                 "content_type": content_type, "reference": reference}
        self.jobs[token] = entry
        if self.mode == "complete-without-output":
            job["status"] = "COMPLETE"
            self.notify(entry)
        elif self.mode != "wait":
            self.complete(entry, self.mode == "corrupt-output")
        return job, self.mode == "lost-response"

    def reference_for(self, input_settings, output):
        audio = output.get("audioDescriptions", [])
        if output["extension"] == "m4a" and len(audio) == 1 and audio[0]["codecSettings"]["codec"] == "AAC":
            return "audio/mp4", self.audio
        if output["extension"] != "mp4":
            raise ValueError("Perfil de saída divergente.")
        channels = self.audio_channels(input_settings, audio)
        video = output["videoDescription"]
        settings = video["codecSettings"]
        h264 = settings["h264Settings"]
        rotation = input_settings["videoSelector"]["rotate"]
        if (video["scalingBehavior"] != "FIT_NO_UPSCALE" or settings["codec"] != "H_264"
            or h264["rateControlMode"] != "QVBR" or h264["qvbrSettings"]["qvbrQualityLevel"] != 7
            or h264["maxBitrate"] != 5_000_000 or h264["framerateControl"] != "SPECIFIED"
            or h264["framerateConversionAlgorithm"] != "DUPLICATE_DROP"
            or (h264["parNumerator"], h264["parDenominator"]) != (1, 1)):
            raise ValueError("Configuração H.264 fora do contrato.")
        rate = Fraction(h264["framerateNumerator"], h264["framerateDenominator"])
        reference = self.video_references[(video["width"], video["height"], rate, rotation, channels)]
        return "video/mp4", reference

    @staticmethod
    def audio_channels(input_settings, descriptions):
        selectors = input_settings.get("audioSelectors", {})
        if not descriptions and not selectors:
            return 0
        audio, = descriptions
        settings = audio["codecSettings"]
        aac = settings["aacSettings"]
        if (settings["codec"] != "AAC" or aac["codecProfile"] != "LC" or aac["sampleRate"] != 48000
            or selectors[audio["audioSourceName"]]["tracks"] != [1]):
            raise ValueError("Faixa AAC fora do contrato.")
        return {"CODING_MODE_1_0": 1, "CODING_MODE_2_0": 2}[aac["codingMode"]]

    @staticmethod
    def input_format(data):
        if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
            return "wav"
        if data.startswith(b"OggS"):
            return "ogg"
        if data.startswith(bytes.fromhex("1a45dfa3")):
            return "webm"
        if data[4:8] == b"ftyp":
            return "iso"
        return "other"

    def complete(self, entry, corrupt=False):
        job = entry["job"]
        if "output_version" in entry:
            return
        body = b"fixture-output-invalid" if corrupt else entry["reference"]
        stored = self.s3.put_object(Bucket=self.bucket, Key=entry["output"], Body=body, ContentType=entry["content_type"])
        entry["output_version"] = stored["VersionId"]
        entry["output_sha"] = sha256(body).hexdigest()
        job["status"] = "COMPLETE"
        self.notify(entry)

    def notify(self, entry):
        job = entry["job"]
        now = datetime.fromisoformat(self.clock.read_text().strip()).astimezone(timezone.utc)
        event = {"version": "0", "id": str(uuid4()), "source": "aws.mediaconvert",
                 "detail-type": "MediaConvert Job State Change", "account": ACCOUNT, "region": REGION,
                 "time": now.isoformat().replace("+00:00", "Z"), "resources": [job["arn"]], "detail": {
                     "accountId": ACCOUNT, "jobId": job["id"], "queue": self.job_queue, "status": "COMPLETE",
                     "userMetadata": job["userMetadata"], "timestamp": int(now.timestamp() * 1000),
                     "outputGroupDetails": [{"type": "FILE_GROUP", "outputDetails": [
                         {"outputFilePaths": [f"s3://{self.bucket}/{entry['output']}"]}]}]}}
        for _ in range(2):
            self.sqs.send_message(QueueUrl=self.queue, MessageBody=json.dumps(event))

    def summary(self):
        return [{"attempt": token, "create_calls": self.requests[token], "provider_job": entry["job"]["id"],
                 "input_sha": entry["input_sha"], "input_version": entry["input_version"],
                 "input_format": entry["input_format"], "output_version": entry.get("output_version"),
                 "output_sha": entry.get("output_sha"), "status": entry["job"]["status"]} for token, entry in self.jobs.items()]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def response(self, status, body, error=None):
        payload = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if error:
            self.send_header("x-amzn-errortype", error)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path).path.rstrip("/")
        with self.server.fixture.lock:
            if path == "/fixture/summary":
                self.response(200, self.server.fixture.summary())
            elif path == API:
                self.response(200, {"jobs": [e["job"] for e in reversed(self.server.fixture.jobs.values())]})
            elif path.startswith(API + "/"):
                job_id = path.removeprefix(API + "/")
                for e in self.server.fixture.jobs.values():
                    if e["job"]["id"] == job_id:
                        self.response(200, {"job": e["job"]})
                        return
                self.response(404, {"message": "Job inexistente na fixture."}, "NotFoundException")
            else:
                self.response(404, {})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > 65_536:
            self.response(400, {"message": "Tamanho fora do contrato da fixture."})
            return
        try:
            data = json.loads(self.rfile.read(size))
            with self.server.fixture.lock:
                if self.path.rstrip("/") == API:
                    job, drop = self.server.fixture.receive(data)
                    if drop:
                        self.close_connection = True
                        return
                    self.response(201, {"job": job})
                elif self.path == "/fixture/mode" and data.get("mode") in MODES:
                    self.server.fixture.mode = data["mode"]
                    self.response(200, {})
                elif self.path == "/fixture/complete":
                    self.server.fixture.complete(self.server.fixture.jobs[data["attempt"]])
                    self.response(200, {})
                elif self.path == "/fixture/replay":
                    entry = self.server.fixture.jobs[data["attempt"]]
                    if entry["job"]["status"] != "COMPLETE":
                        raise ValueError("O replay requer resultado terminal.")
                    self.server.fixture.notify(entry)
                    self.response(200, {})
                else:
                    self.response(404, {})
        except (KeyError, TypeError, ValueError) as error:
            print(type(error).__name__, str(error), flush=True)
            self.response(400, {"message": "Contrato divergente na fixture."}, "BadRequestException")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 5000), Handler)
    server.fixture = ProviderFixture()
    server.serve_forever()
