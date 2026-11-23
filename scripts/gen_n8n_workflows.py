"""Generate the 9 importable n8n workflows into n8n/workflows/.

All workflows talk to the worker API (ASS_API_URL) with the X-API-Key header taken from
the n8n container environment. Webhooks require header auth (credential "ASS Webhook Auth").
Nothing here executes shell commands.

Run: .venv/bin/python scripts/gen_n8n_workflows.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "n8n" / "workflows"
NS = uuid.UUID("6f1c2a4e-8b3d-4f6a-9c2e-1a2b3c4d5e6f")

API_HEADERS = {"parameters": [{"name": "X-API-Key", "value": "={{ $env.ASS_API_KEY }}"}]}
RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 3000}
WEBHOOK_CRED = {"httpHeaderAuth": {"id": "ass-webhook-auth", "name": "ASS Webhook Auth"}}
TELEGRAM_CRED = {"telegramApi": {"id": "ass-telegram", "name": "Audio Story Bot"}}

VALIDATE_EPISODE_JS = """// Validate webhook input: only integers / safe run tags pass through.
const body = $input.first().json.body || {};
const episodeId = Number(body.episode_id);
if (!Number.isInteger(episodeId) || episodeId <= 0) {
  throw new Error('episode_id must be a positive integer');
}
const run = String(body.run || 'n8n-' + Date.now());
if (!/^[A-Za-z0-9_-]{1,40}$/.test(run)) throw new Error('invalid run tag');
const chatId = body.chat_id === undefined || body.chat_id === null ? null : Number(body.chat_id);
if (chatId !== null && !Number.isInteger(chatId)) throw new Error('chat_id must be an integer');
return [{ json: { episode_id: episodeId, run, chat_id: chatId } }];"""


def nid(wf: str, name: str) -> str:
    return str(uuid.uuid5(NS, f"{wf}/{name}"))


def node(wf, name, ntype, version, params, pos, **extra):
    n = {"parameters": params, "id": nid(wf, name), "name": name, "type": ntype,
         "typeVersion": version, "position": pos}
    n.update(extra)
    return n


def webhook(wf, path, pos=(0, 0)):
    return node(wf, "Webhook", "n8n-nodes-base.webhook", 2,
                {"httpMethod": "POST", "path": path, "authentication": "headerAuth",
                 "responseMode": "responseNode", "options": {}},
                list(pos), webhookId=nid(wf, "webhook-id"), credentials=WEBHOOK_CRED)


def validate(wf, pos):
    return node(wf, "Validate input", "n8n-nodes-base.code", 2, {"jsCode": VALIDATE_EPISODE_JS}, list(pos),
                onError="continueErrorOutput")


def api_call(wf, name, method, url_expr, pos, body_expr=None):
    params = {"method": method, "url": url_expr, "sendHeaders": True, "headerParameters": API_HEADERS,
              "options": {"timeout": 30000}}
    if body_expr:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": body_expr})
    return node(wf, name, "n8n-nodes-base.httpRequest", 4.2, params, list(pos),
                onError="continueErrorOutput", **RETRY)


def respond(wf, name, pos, code=200, body="={{ JSON.stringify($json) }}"):
    return node(wf, name, "n8n-nodes-base.respondToWebhook", 1.1,
                {"respondWith": "json", "responseBody": body, "options": {"responseCode": code}}, list(pos))


def notify(wf, name, text_expr, pos):
    return api_call(wf, name, "POST", "={{ $env.ASS_API_URL }}/api/notify", pos,
                    body_expr="={{ JSON.stringify({ text: " + text_expr + " }) }}")


def conn(*pairs):
    """pairs: (from, output_index, to)."""
    out: dict = {}
    for src, idx, dst in pairs:
        main = out.setdefault(src, {"main": []})["main"]
        while len(main) <= idx:
            main.append([])
        main[idx].append({"node": dst, "type": "main", "index": 0})
    return out


def workflow(wf_id, name, nodes, connections, tags):
    return {"name": name, "nodes": nodes, "connections": connections, "active": False,
            "settings": {"executionOrder": "v1", "saveDataErrorExecution": "all",
                         "saveDataSuccessExecution": "all", "executionTimeout": 120},
            "pinData": {}, "meta": {"templateCredsSetupCompleted": False},
            "id": ("AudioStory" + wf_id.replace("-", "")).ljust(16, "0")[:16], "tags": []}


def stage_workflow(wf, title, path, stage, description):
    """Webhook -> validate -> enqueue stage job (async) -> respond 202 with job id."""
    url = "={{ $env.ASS_API_URL }}/api/episodes/{{ $json.episode_id }}/stages/" + stage
    body = "={{ JSON.stringify({ run: $json.run, chat_id: $json.chat_id }) }}"
    nodes = [
        webhook(wf, path, (0, 0)),
        validate(wf, (240, 0)),
        api_call(wf, f"Enqueue {stage}", "POST", url, (480, -80), body),
        respond(wf, "Respond queued", (720, -160), 202),
        notify(wf, "Notify failure", f"'⚠️ {wf} enqueue {stage} failed: ' + ($json.error?.message || 'unknown')",
               (720, 40)),
        respond(wf, "Respond error", (960, 40), 502, "={{ JSON.stringify({ ok: false, error: 'enqueue failed' }) }}"),
        respond(wf, "Respond invalid", (480, 120), 400,
                "={{ JSON.stringify({ ok: false, error: $json.error?.message || 'invalid input' }) }}"),
        node(wf, "About", "n8n-nodes-base.stickyNote", 1,
             {"content": f"## {title}\n{description}\n\nPOST /webhook/{path}\n"
                         "Header: as configured in 'ASS Webhook Auth'\n"
                         "Body: {\"episode_id\": 1, \"run\": \"r1\", \"chat_id\": 123}\n\n"
                         "Idempotent: same episode_id+run → same job.", "height": 260, "width": 380},
             [0, -320]),
    ]
    connections = conn(("Webhook", 0, "Validate input"), ("Validate input", 0, f"Enqueue {stage}"),
                       ("Validate input", 1, "Respond invalid"), (f"Enqueue {stage}", 0, "Respond queued"),
                       (f"Enqueue {stage}", 1, "Notify failure"), ("Notify failure", 0, "Respond error"),
                       ("Notify failure", 1, "Respond error"))
    return workflow(wf, title, nodes, connections, [])


def wf00():
    wf = "WF-00"
    allow_js = """// Allowlist: only configured Telegram user IDs may control the studio.
const allowed = String($env.TELEGRAM_ALLOWED_USER_IDS || '').split(',').map(s => s.trim()).filter(Boolean);
const u = $input.first().json;
const from = (u.message && u.message.from) || (u.callback_query && u.callback_query.from) || {};
if (!allowed.includes(String(from.id))) {
  return [];  // silently drop unauthorised users (the worker API re-checks as well)
}
return [{ json: u }];"""
    nodes = [
        node(wf, "Telegram Trigger", "n8n-nodes-base.telegramTrigger", 1.1,
             {"updates": ["message", "callback_query"], "additionalFields": {}}, [0, 0],
             webhookId=nid(wf, "tg-webhook"), credentials=TELEGRAM_CRED),
        node(wf, "Allowlist", "n8n-nodes-base.code", 2, {"jsCode": allow_js}, [240, 0]),
        api_call(wf, "Forward to worker API", "POST", "={{ $env.ASS_API_URL }}/api/telegram/update", [480, 0],
                 body_expr="={{ JSON.stringify($json) }}"),
        node(wf, "Log error", "n8n-nodes-base.code", 2,
             {"jsCode": "console.error('worker API unreachable', JSON.stringify($input.all().map(i => i.json)));\n"
                        "return $input.all();"}, [720, 100]),
        node(wf, "About", "n8n-nodes-base.stickyNote", 1,
             {"content": "## WF-00 Telegram Controller\nTelegram Trigger (HTTPS webhook) → allowlist → worker API.\n"
                         "The API parses commands (/start /newstory /create_episode /status /preview /approve "
                         "/regen_scene /budget /cancel /publish) and inline buttons, enqueues jobs and replies.\n"
                         "Never runs shell. Needs WEBHOOK_URL=https://… for n8n.", "height": 260, "width": 420},
             [0, -320]),
    ]
    connections = conn(("Telegram Trigger", 0, "Allowlist"), ("Allowlist", 0, "Forward to worker API"),
                       ("Forward to worker API", 1, "Log error"))
    return workflow(wf, "WF-00 Telegram Controller", nodes, connections, [])


def wf01():
    wf = "WF-01"
    validate_js = """const body = $input.first().json.body || {};
const path = String(body.path || '');
if (!/^[\\w\\/.\\- ]{1,300}\\.(md|txt)$/.test(path) || path.includes('..')) throw new Error('invalid story path');
return [{ json: { path } }];"""
    nodes = [
        webhook(wf, "ass/wf01-intake", (0, 0)),
        node(wf, "Validate input", "n8n-nodes-base.code", 2, {"jsCode": validate_js}, [240, 0],
             onError="continueErrorOutput"),
        api_call(wf, "Import story", "POST", "={{ $env.ASS_API_URL }}/api/stories/import", [480, -80],
                 body_expr="={{ JSON.stringify({ path: $json.path }) }}"),
        node(wf, "Rights pending?", "n8n-nodes-base.if", 2,
             {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                             "conditions": [{"id": nid(wf, "cond"), "leftValue": "={{ $json.rights_status }}",
                                             "rightValue": "approved",
                                             "operator": {"type": "string", "operation": "notEquals"}}],
                             "combinator": "and"}, "options": {}}, [720, -80]),
        notify(wf, "Ask rights approval",
               "'📚 Truyện #' + $json.story_id + ' cần xác nhận bản quyền: /approve rights ' + $json.story_id",
               [960, -160]),
        respond(wf, "Respond", (1200, -80), 200, "={{ JSON.stringify($('Import story').item.json) }}"),
        respond(wf, "Respond invalid", (480, 120), 400,
                "={{ JSON.stringify({ ok: false, error: $json.error?.message || 'invalid input' }) }}"),
        respond(wf, "Respond error", (720, 120), 502, "={{ JSON.stringify({ ok: false, error: 'import failed' }) }}"),
        node(wf, "About", "n8n-nodes-base.stickyNote", 1,
             {"content": "## WF-01 Story Intake & Rights Approval\nImports a story file already placed under "
                         "storage/stories or samples/. Rights start 'pending'; the approval gate is "
                         "/approve rights <id> in Telegram (or POST /api/approvals/{id}/decision).",
              "height": 220, "width": 420}, [0, -320]),
    ]
    connections = conn(("Webhook", 0, "Validate input"), ("Validate input", 0, "Import story"),
                       ("Validate input", 1, "Respond invalid"), ("Import story", 0, "Rights pending?"),
                       ("Import story", 1, "Respond error"), ("Rights pending?", 0, "Ask rights approval"),
                       ("Rights pending?", 1, "Respond"), ("Ask rights approval", 0, "Respond"),
                       ("Ask rights approval", 1, "Respond"))
    return workflow(wf, "WF-01 Story Intake & Rights Approval", nodes, connections, [])


def wf02():
    wf = stage_workflow("WF-02", "WF-02 Script Builder", "ass/wf02-script", "script_build",
                        "Normalises narration, splits sections/paragraphs/sentences, tags characters and "
                        "locations → episode_script.json. AUTO_CHAIN continues through story validation, TTS and mandatory audio QC.")
    return wf


def wf07():
    wf = "WF-07"
    base = stage_workflow(wf, "WF-07 Render, QC & Publishing Queue", "ass/wf07-render", "render_preview",
                          "Renders the 720p preview (cached per-scene clips), runs QC and sends the preview "
                          "with approval buttons. Final 1080p only after content approval; publishing only "
                          "after publish approval (ALLOW_AUTO_PUBLISH=false → ready_manual).")
    # Worker → n8n events (N8N_EVENT_WEBHOOK_URL): only final `stage.failed`. The worker already notifies
    # Telegram itself, so this hook only acknowledges; attach custom handling (logging, alerts) after it.
    base["nodes"] += [
        webhook(wf + "-events", "ass/events", (0, 360)) | {"name": "Pipeline events",
                                                           "id": nid(wf, "Pipeline events")},
        respond(wf, "Ack event", (240, 360), 200, "={{ JSON.stringify({ ok: true }) }}"),
        node(wf, "About events", "n8n-nodes-base.stickyNote", 1,
             {"content": "## Pipeline events\nWorker POSTs only final `stage.failed` "
                         "{event, job_id, type, episode_id, error}.\nTelegram is already notified by the "
                         "worker — do NOT notify again here (duplicates).", "height": 180, "width": 380},
             [0, 520]),
    ]
    base["connections"].update(conn(("Pipeline events", 0, "Ack event")))
    return base


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    flows = {
        "WF-00_telegram_controller.json": wf00(),
        "WF-01_story_intake_rights.json": wf01(),
        "WF-02_script_builder.json": wf02(),
        "WF-03_tts_production.json": stage_workflow(
            "WF-03", "WF-03 TTS Production", "ass/wf03-tts", "tts",
            "Local Vietnamese TTS (macOS/Piper/optional offline VieNeu), immutable chunk cache, source audio QC, "
            "transcript_timeline.json from real chunk durations."),
        "WF-04_visual_story_planning.json": stage_workflow(
            "WF-04", "WF-04 Visual Story Planning", "ass/wf04-visual-plan", "visual_plan",
            "Scene boundaries on audio timestamps (45–150s, target 120s), prompts, asset matching → "
            "scene_plan.json + image_generation_queue.json."),
        "WF-05_visual_asset_generation.json": stage_workflow(
            "WF-05", "WF-05 Visual Asset Generation", "ass/wf05-visual-generate", "visual_generate",
            "Generates only missing images (≤ MAX_NEW_IMAGES_PER_EPISODE) with mock/codex/local provider, "
            "budget reservation per image → scene_assets_map.json."),
        "WF-06_timeline_composer.json": stage_workflow(
            "WF-06", "WF-06 Timeline Composer", "ass/wf06-timeline", "timeline",
            "Frame-exact scene timing, motion, transitions, subtitles and a verified audio master → timeline.json."),
        "WF-07_render_qc_publishing.json": wf07(),
        "WF-08_audio_production_qc.json": stage_workflow(
            "WF-08", "WF-08 Horror Audio Production & QC", "ass/wf08-audio", "tts_qc",
            "Analyze existing real TTS → safe repair (max 2 attempts) → licensed/procedural sound assets → "
            "sound cues → 5-stem ducked mix → 2-pass master → master QC → existing visual/render chain. "
            "FAIL blocks publication. Uncertain defects export review clips and require file-bound human review. "
            "No paid generation. Existing stage URLs and job contracts remain valid."),
    }
    for name, wf in flows.items():
        (OUT / name).write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote", name, len(wf["nodes"]), "nodes")


if __name__ == "__main__":
    main()
