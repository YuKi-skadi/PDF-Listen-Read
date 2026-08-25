# PDF Local TTS Bridge Protocol

This document defines the HTTP contract used by the PDF reader's `local_bridge` audio provider.

## Base URL and health check

The user enters a bridge base URL such as `http://127.0.0.1:47840` in the PDF reader.

The reader checks:

```http
GET /health
```

The bridge should return HTTP 200 and JSON. The recommended response is:

```json
{
  "ok": true,
  "service": "pdf-local-tts-bridge",
  "model_loaded": false,
  "backend": "cuda"
}
```

## Submit a job

```http
POST /v1/jobs
Content-Type: application/json
```

Request body:

```json
{
  "schema_version": "pdf-local-tts-1",
  "job_type": "pdf_audio",
  "paper_id": "paper id from the PDF reader",
  "text_version_id": "exact text version id",
  "text_version_label": "读图识别",
  "model": "Qwen3-TTS-12Hz-0.6B-CustomVoice",
  "voice": "Vivian",
  "voice_id": "Vivian",
  "speed": 1.0,
  "voice_mode": "custom_voice",
  "reference_audio": "",
  "reference_text": "",
  "instruct": "",
  "target_package_id": "",
  "segments": [
    {
      "index": 0,
      "segment_id": "local text segment id",
      "content_hash": "sha256 from the PDF reader",
      "text": "text to synthesize"
    }
  ]
}
```

`voice_mode` may be `custom_voice` or `voice_clone`. For `voice_clone`, the
bridge can use `reference_audio` as a local absolute path and optionally use
`reference_text` to improve the clone prompt. `instruct` is an optional
expression instruction. These fields may be omitted when the bridge's own
default voice settings should be used.

The bridge returns HTTP 200, 201, or 202 and a job ID:

```json
{
  "job": {
    "id": "bridge job id",
    "status": "queued",
    "progress": 0,
    "message": "任务已排队"
  }
}
```

## Job status and cancellation

```http
GET /v1/jobs/{job_id}
POST /v1/jobs/{job_id}/cancel
```

Status values should be one of `queued`, `running`, `completed`, `completed_with_errors`, `failed`, or `cancelled`.
`progress` may be a fraction from 0 to 1 or a percentage from 0 to 100.

## Download the result

After a completed job:

```http
GET /v1/jobs/{job_id}/package
```

The response is a ZIP file. The ZIP must contain `manifest.json`, one audio file for every successful segment, and optionally a complete audio file.

Example `manifest.json`:

```json
{
  "schema_version": "pdf-local-tts-1",
  "job_id": "bridge job id",
  "package_id": "stable package id",
  "paper_id": "same paper id",
  "text_version_id": "same text version id",
  "provider": "local_bridge",
  "model": "Qwen3-TTS-12Hz-0.6B-CustomVoice",
  "voice_id": "Vivian",
  "voice_mode": "custom_voice",
  "speed": 1.0,
  "segments": [
    {
      "segment_id": "same segment id",
      "index": 0,
      "content_hash": "same sha256",
      "file": "segments/00000.wav",
      "duration_ms": 2400
    }
  ],
  "full_audio": {
    "file": "full.wav",
    "duration_ms": 2400
  }
}
```

WAV, OGG, FLAC, M4A, or MP3 are accepted. The PDF reader validates the paper ID, text version ID, segment IDs, indexes, and content hashes before saving anything. A mismatched or unsafe ZIP is rejected.
