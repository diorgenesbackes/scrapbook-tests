import argparse
from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import unittest
from uuid import UUID

from audio_media_processing_stack import AudioMediaProcessingStackTests, FAILURE_FACT
from docker_stack import ROOT
from photo_submission_stack import utc_timestamp
from video_submission_stack import VideoSubmissionStackTests


@dataclass(frozen=True)
class VideoCase:
    name: str
    source: Path
    width: int
    height: int
    rate: str
    channels: int
    rotation: str = "DEGREE_0"
    input_format: str = "iso"


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
        capture_output=True, check=True, timeout=50)


def source_video(target, width, height, rate, *, seconds=3, audio=False, filters=(), codec=None, rotation=None):
    video = ("-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={rate}")
    sound = ("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000") if audio else ()
    webm = target.suffix == ".webm"
    codec = codec or ("libvpx-vp9" if webm else "libx264")
    encoding = ("-deadline", "realtime", "-cpu-used", 8) if webm else ("-preset", "ultrafast")
    audio_output = ("-c:a", "libopus" if webm else "aac", "-ac", 1) if audio else ("-an",)
    conversions = ("-vf", ",".join(filters), "-fps_mode", "vfr") if filters else ()
    timescale = () if webm else ("-video_track_timescale", 24000)
    unrotated = target.with_name("unrotated" + target.suffix) if rotation else target
    ffmpeg(*video, *sound, "-t", seconds, "-filter_threads", 1, *conversions, "-pix_fmt", "yuv420p",
        "-threads", 1, "-c:v", codec, *encoding, *audio_output, *timescale, unrotated)
    if rotation:
        ffmpeg("-display_rotation", rotation, "-i", unrotated, "-c", "copy", target)
        unrotated.unlink()
    return target


def reference_video(case, target):
    sound = ("-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-ar", 48000, "-ac", case.channels,
        "-b:a", "96k" if case.channels == 1 else "128k") if case.channels else ("-an",)
    ffmpeg("-i", case.source, "-map", "0:v:0", "-map_metadata", -1, "-map_chapters", -1,
        "-filter_threads", 1, "-vf", f"scale={case.width}:{case.height}:flags=bilinear,setsar=1,fps={case.rate}",
        "-threads", 1, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        *sound, "-movflags", "+faststart", target)


class VideoMediaProcessingStackTests(AudioMediaProcessingStackTests, VideoSubmissionStackTests):
    def prepare_video_references(self):
        workspace = self.stack.env_file.parent
        self.video_references = workspace / "video-references"
        self.video_references.mkdir()
        self.cases = [
            VideoCase("paisagem", source_video(workspace / "landscape.mp4", 320, 180, 25, audio=True),
                320, 180, "25", 1),
            VideoCase("rotação", source_video(workspace / "portrait.mov", 320, 180, 24, rotation=90),
                180, 320, "24", 0, "DEGREES_270"),
            VideoCase("quadrado", source_video(workspace / "square.webm", 240, 240, 60, audio=True),
                240, 240, "30", 1, input_format="webm"),
            VideoCase("anamórfico", source_video(workspace / "anamorphic.mp4", 320, 240, 24, filters=("setsar=4/3",)),
                320, 180, "24", 0),
            VideoCase("VFR", source_video(workspace / "vfr.mp4", 256, 144, 24,
                filters=("select='gte(t,1.5)+not(mod(n,2))'",)), 256, 144, "12", 0),
            VideoCase("limite HD", source_video(workspace / "large.mp4", 2048, 1152, 24),
                1920, 1080, "24", 0),
        ]
        browser = VideoCase("navegador", ROOT / "media/fixtures/short.mov", 640, 360, "24", 0)
        catalog = []
        for index, case in enumerate([*self.cases, browser]):
            reference = f"{index}.mp4"
            reference_video(case, self.video_references / reference)
            catalog.append({"file": reference, "width": case.width, "height": case.height, "rate": case.rate,
                            "rotation": case.rotation, "channels": case.channels})
        (self.video_references / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        self.browser_case = browser

    def submit_video(self, code, sample):
        self.set_time(utc_timestamp(self.admission))
        draft = self.video_draft(code, sample, "testimonial")
        path, bearer = self.admit(code, draft)
        payload = sample.read_bytes()
        key = draft["files"][0]["fileKey"]
        uploaded, version = self.upload(payload, self.authorize_upload(path, bearer, key))
        self.complete_video(path, bearer)
        self.notify_uploaded(uploaded, version, self.admission, len(payload))
        receipt = self.wait_for_status(path, bearer, "confirmed")
        self.assertEqual({"state", "confirmedAt"}, set(receipt))
        file, = self.file_ids(path)
        return (path, bearer, receipt), draft, file, payload

    def verify_video(self, file, case, *, input_format=None):
        row = self.await_audio(lambda: self.ready_audio(file), "O vídeo confirmado não ficou pronto.", timeout=55)
        _, _, _, _, attempt, version, checksum, key, media_type, audio_codec, duration, submitted, provider_id,             stage_sha, stage_version = row
        self.assertEqual(("video/mp4", "aac" if case.channels else "", "t"), (media_type, audio_codec, submitted))
        self.assertGreater(float(duration), 2.75)
        self.assertLess(float(duration), 3.25)
        self.assertEqual(f"submissions/transcode-output/{file}/{UUID(attempt).hex}/playback.mp4", key)
        info, = [item for item in self.provider("/fixture/summary") if item["attempt"] == UUID(attempt).hex]
        self.assertEqual((provider_id, version, checksum, stage_version, stage_sha),
            (info["provider_job"], info["output_version"], info["output_sha"], info["input_version"], info["input_sha"]))
        self.assertEqual(input_format or case.input_format, info["input_format"])
        metadata = self.stack.app_sql(f"""
            SELECT video_codec, width, height, frame_rate_numerator, frame_rate_denominator, location_removed
            FROM scrapbook.file_variants
            WHERE file_variants_files_id = {file} AND kind = 'playback' AND state = 'ready';
        """).split("|")
        codec, width, height, numerator, denominator, removed = metadata
        self.assertEqual(("h264", case.width, case.height, "t"), (codec, int(width), int(height), removed))
        self.assertEqual(Fraction(case.rate), Fraction(int(numerator), int(denominator)))
        return row

    def verify_matrix(self, code):
        for case in self.cases:
            with self.subTest(case=case.name):
                submission, draft, file, payload = self.submit_video(code, case.source)
                self.verify_video(file, case)
                self.assert_original(file, payload)
                self.assert_receipt(code, draft, submission)
                print(f"{case.name}: variante H.264 verificada, recibo e original preservados.", flush=True)

    def verify_missing_output(self, code):
        self.provider("/fixture/mode", {"mode": "complete-without-output"})
        case = self.cases[0]
        submission, draft, file, payload = self.submit_video(code, case.source)

        def deferred():
            error = self.stack.app_sql(f"""
                SELECT coalesce(last_error_code, '') FROM scrapbook.processing_jobs
                WHERE processing_jobs_files_id = {file};
            """)
            return self.audio_row(file) if error == "MEDIA_VARIANT_STORAGE_UNAVAILABLE" else None

        pending = self.await_audio(deferred, "Resultado completo sem objeto deveria ser reconciliado.")
        self.assertEqual(("processing", ""), (pending[0], pending[5]))
        self.assert_receipt(code, draft, submission)
        self.provider("/fixture/complete", {"attempt": UUID(pending[4]).hex})
        ready = self.verify_video(file, case)
        self.assertEqual(pending[4], ready[4])
        self.assert_original(file, payload)
        print("Resultado completo sem objeto ficou em processamento até a versão ser verificável.", flush=True)

    def verify_reconciliation(self, code):
        self.provider("/fixture/mode", {"mode": "lost-response"})
        case = self.cases[1]
        submission, draft, file, payload = self.submit_video(code, case.source)
        ready = self.verify_video(file, case)
        self.assertEqual("1", self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.processing_attempts WHERE processing_attempts_files_id = {file};
        """))
        self.provider("/fixture/replay", {"attempt": UUID(ready[4]).hex})

        def drained():
            record = self.aws(("sqs", "get-queue-attributes"), "--queue-url", self.result_queue,
                "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible")
            return all(value == "0" for value in record["Attributes"].values())

        self.await_audio(drained, "A fila não drenou a entrega tardia.")
        self.assertEqual(ready, self.audio_row(file))
        self.assert_original(file, payload)
        self.assert_receipt(code, draft, submission)
        print("Resultado antecipado e reentrega após prontidão preservaram a mesma versão.", flush=True)

    def verify_corrupted_video(self, code):
        self.provider("/fixture/mode", {"mode": "corrupt-output"})
        submission, draft, file, payload = self.submit_video(code, self.cases[0].source)

        def failed_without_variant():
            state = self.audio_row(file)
            return state if state[:4] == ["failed", "failed", "failed", "failed"] else None

        failure = self.await_audio(failed_without_variant, "A saída inválida não foi classificada como falha do arquivo.")
        self.assertEqual("", failure[5])
        self.assertEqual("1", self.stack.app_sql(f"""
            SELECT count(*) FROM scrapbook.outbox_messages AS failure
            JOIN scrapbook.outbox_messages AS confirmation ON confirmation.message_id = failure.causation_id
            WHERE failure.type = '{FAILURE_FACT}' AND failure.correlation_id = confirmation.correlation_id
                AND failure.payload->>'fileId' = '{file}' AND failure.payload->>'stage' = 'verify';
        """))
        self.assert_original(file, payload)
        self.assert_receipt(code, draft, submission)
        print("Saída corrompida ficou indisponível e gerou evidência F13 correlacionada.", flush=True)

    def verify_browser_video(self):
        self.provider("/fixture/mode", {"mode": "complete"})
        event, first = self.create_photo_event("Envio de WebM em navegador")
        secondary, second = self.create_photo_event("Envio de MOV em navegador")
        self.run_video_browser(first, second, self.browser_case.source, grep=r"envia short\.(mov|webm)")
        records = self.stack.app_sql(f"""
            SELECT f.id, f.container FROM scrapbook.files AS f
            JOIN scrapbook.records AS r ON r.id = f.files_records_id
            WHERE r.records_events_id IN ({event}, {secondary}) AND r.state = 'confirmed' ORDER BY f.id;
        """).splitlines()
        self.assertEqual(2, len(records))
        for record in records:
            file, container = record.split("|")
            self.verify_video(int(file), self.browser_case, input_format="webm" if container == "webm" else "iso")

    def test_video_processing(self):
        self.prepare_video_references()
        self.configure_audio_processing(enable_video=True)
        _, code = self.create_photo_event("Preparação de vídeos confirmados")
        self.verify_matrix(code)
        self.verify_missing_output(code)
        self.verify_reconciliation(code)
        self.verify_corrupted_video(code)
        if self.include_browser:
            self.verify_browser_video()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valida o fluxo F08→F10 de vídeo em uma stack descartável.")
    parser.add_argument("--browser", action="store_true", help="Executa também os envios MOV e WebM via Playwright.")
    VideoMediaProcessingStackTests.include_browser = parser.parse_args().browser
    suite = unittest.TestSuite([VideoMediaProcessingStackTests("test_video_processing")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
