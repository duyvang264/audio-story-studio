"""Publishing gates bind measurements and listening decisions to immutable file hashes."""
from pathlib import Path
import json
from .io import digest, dump, identity
from . import ENGINE_VERSION


class AudioQCError(RuntimeError):
    retryable = False


class ListeningReviewRequired(AudioQCError):
    pass


def assert_report(report_path: Path | str, expected_file: Path | str | None = None, seen=None, *, allow_review=False) -> dict:
    path=Path(report_path).resolve();seen=set() if seen is None else seen
    if path in seen:
        raise AudioQCError("Circular audio QC dependency")
    seen=seen|{path}
    if not path.is_file():
        raise AudioQCError(f"Mandatory audio QC report missing: {path}")
    r=json.loads(path.read_text());item=r.get("input",{})
    if r.get('engine_version')!=ENGINE_VERSION:
        raise AudioQCError('QC engine version changed or missing; reanalyze before publishing')
    source=Path(item.get("path",""))
    if expected_file is not None and source.resolve()!=Path(expected_file).resolve():
        raise AudioQCError("QC report belongs to a different audio/video file")
    if not source.is_file() or not item.get("sha256") or digest(source)!=item["sha256"]:
        raise AudioQCError("Audio/video changed after QC; reanalyze the actual encoded file")
    if r.get("status")=="FAIL" or any(f.get("severity")=="critical" for f in r.get("flags",[])):
        raise AudioQCError(f"Critical audio QC failure: {path}")
    if r.get("status") not in ("PASS","WARNING","NEEDS_LISTENING_REVIEW"):
        raise AudioQCError("Unknown QC status; refusing publication")
    for dependency in r.get("dependencies",[]):
        dep=Path(dependency["path"])
        if not dep.is_file() or digest(dep)!=dependency["sha256"]:
            raise AudioQCError(f"Stale/missing QC dependency: {dep}")
        if dependency.get("kind")=="report":
            assert_report(dep,seen=seen,allow_review=allow_review)
    unresolved = [f for f in r.get('flags', []) if f.get('severity') == 'review' and f.get('repair_status') != 'repaired']
    requires_review = bool(unresolved) or (r.get('status')=='NEEDS_LISTENING_REVIEW' and not any(f.get('severity')=='review' for f in r.get('flags',[])))
    if requires_review and not allow_review:
        review=path.with_name("listening_review.json")
        decision=json.loads(review.read_text()) if review.is_file() else {}
        if not (decision.get("decision")=="accepted" and decision.get("report_sha256")==digest(path)
                and decision.get("input_sha256")==item["sha256"] and decision.get("reviewer") and decision.get("note")):
            raise ListeningReviewRequired(f"Listen to review clips and record a file-bound decision: {path}")
    return r


def record_review(report_path: Path | str, decision: str, reviewer: str, note: str) -> Path:
    path=Path(report_path);r=json.loads(path.read_text())
    if decision not in ("accepted","rejected") or not reviewer.strip() or not note.strip():
        raise ValueError("A listening decision requires reviewer and a substantive note")
    if (r["status"]=="FAIL" or any(f.get('severity')=='critical' for f in r.get('flags',[]))) and decision=="accepted":
        raise AudioQCError("Listening approval cannot override critical QC failures")
    if identity(r["input"]["path"])["sha256"]!=r["input"]["sha256"]:
        raise AudioQCError("File changed after measurement")
    out=path.with_name("listening_review.json")
    dump(out,{"decision":decision,"reviewer":reviewer.strip(),"note":note.strip(),"report_sha256":digest(path),"input_sha256":r["input"]["sha256"]})
    return out


def dependency(path: Path, kind="file") -> dict:
    return {**identity(path),"kind":kind}
