"""
Plain-Python helpers wrapping the SQLAlchemy models - this is the same
business logic that used to live in the FastAPI route handlers, just
called directly as functions instead of over HTTP.
"""
from datetime import datetime
import models
from database import SessionLocal


def get_previous_wave_check_result(db, study_id: int, current_wave_id: int):
    """Most recent check result from a different (earlier) wave in the same
    study - used to give the AI wave-over-wave comparison context."""
    other_waves = db.query(models.Wave).filter(
        models.Wave.study_id == study_id, models.Wave.id != current_wave_id
    ).order_by(models.Wave.wave_number.desc()).all()
    for prev_wave in other_waves:
        prev_upload = db.query(models.DataUpload).filter(
            models.DataUpload.wave_id == prev_wave.id
        ).order_by(models.DataUpload.checkpoint_pct.desc(), models.DataUpload.uploaded_at.desc()).first()
        if prev_upload:
            prev_result = db.query(models.CheckResult).filter(
                models.CheckResult.upload_id == prev_upload.id
            ).first()
            if prev_result:
                return prev_wave, prev_upload, prev_result
    return None, None, None


def get_latest_upload_for_stage(db, wave_id: int, stage: str):
    """
    stage="soft" -> the soft-launch upload (checkpoint_pct == 0)
    stage="full" -> the most advanced full-launch upload (checkpoint_pct > 0),
                    preferring the highest checkpoint_pct, then most recent
    """
    query = db.query(models.DataUpload).filter(models.DataUpload.wave_id == wave_id)
    if stage == "soft":
        query = query.filter(models.DataUpload.checkpoint_pct == 0)
        return query.order_by(models.DataUpload.uploaded_at.desc()).first()
    else:
        query = query.filter(models.DataUpload.checkpoint_pct > 0)
        return query.order_by(models.DataUpload.checkpoint_pct.desc(),
                               models.DataUpload.uploaded_at.desc()).first()


def list_studies(db):
    return db.query(models.Study).order_by(models.Study.id.desc()).all()


def get_study(db, study_id):
    return db.query(models.Study).filter(models.Study.id == study_id).first()


def create_study(db, name: str) -> models.Study:
    study = models.Study(name=name)
    db.add(study)
    db.commit()
    db.refresh(study)
    return study


def list_waves(db, study_id: int):
    return db.query(models.Wave).filter(models.Wave.study_id == study_id).order_by(models.Wave.wave_number).all()


def get_wave(db, wave_id: int):
    return db.query(models.Wave).filter(models.Wave.id == wave_id).first()


def create_wave(db, study_id: int, wave_number: int, wave_type: str, sample_size, wave_label: str = None) -> models.Wave:
    wave = models.Wave(study_id=study_id, wave_number=wave_number,
                        wave_type=wave_type, sample_size=sample_size, wave_label=wave_label)
    db.add(wave)
    db.commit()
    db.refresh(wave)
    return wave


def list_quota_rules(db, study_id: int):
    return db.query(models.QuotaRule).filter(models.QuotaRule.study_id == study_id).all()


def add_quota_rule(db, study_id, quota_group, quota_label, column_name, match_value, target_n, quota_type="soft"):
    rule = models.QuotaRule(study_id=study_id, quota_group=quota_group, quota_label=quota_label,
                             column_name=column_name, match_value=match_value,
                             target_n=target_n, quota_type=quota_type)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def delete_quota_rule(db, rule_id: int):
    rule = db.query(models.QuotaRule).filter(models.QuotaRule.id == rule_id).first()
    if rule:
        db.delete(rule)
        db.commit()


def wave_history(db, wave_id: int):
    uploads = db.query(models.DataUpload).filter(models.DataUpload.wave_id == wave_id).all()
    out = []
    for u in uploads:
        cr = db.query(models.CheckResult).filter(models.CheckResult.upload_id == u.id).first()
        out.append({
            "upload_id": u.id, "checkpoint_pct": u.checkpoint_pct,
            "uploaded_at": u.uploaded_at, "check_result": cr,
        })
    return out
