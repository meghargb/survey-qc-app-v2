from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class User(Base):
    """A person who can log into the app with their own email + password.
    Everyone shares the same projects/studies - this just gates access and
    lets each person have their own login instead of one shared password."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)  # "salt_hex:hash_hex", see auth.py
    created_at = Column(DateTime, default=datetime.utcnow)


class Study(Base):
    """One market research project/study. Can contain multiple waves."""
    __tablename__ = "studies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    stage = Column(String, default="soft_launch")  # soft_launch | full_launch | complete
    decipher_link = Column(String, nullable=True)
    questionnaire_file_path = Column(String, nullable=True)
    data_map = Column(Text, nullable=True)  # user-provided variable -> meaning mapping (free text)
    created_at = Column(DateTime, default=datetime.utcnow)

    waves = relationship("Wave", back_populates="study")
    quota_rules = relationship("QuotaRule", back_populates="study")


class Wave(Base):
    """A wave (Wave 1, Wave 2...) belonging to a study."""
    __tablename__ = "waves"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("studies.id"))
    wave_number = Column(Integer, nullable=False)   # always set (1, 2, 3...) - used for ordering
    wave_label = Column(String, nullable=True)       # optional custom name, e.g. "Pre-Wave", "Post-Wave"
    wave_type = Column(String, default="single")   # single | multiple
    sample_size = Column(Integer, nullable=True)    # target sample size for this wave
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def display_name(self) -> str:
        return self.wave_label.strip() if self.wave_label and self.wave_label.strip() else f"Wave {self.wave_number}"

    study = relationship("Study", back_populates="waves")
    uploads = relationship("DataUpload", back_populates="wave")


class DataUpload(Base):
    """One data snapshot upload at a given completion checkpoint (25/50/75/100%)."""
    __tablename__ = "data_uploads"

    id = Column(Integer, primary_key=True, index=True)
    wave_id = Column(Integer, ForeignKey("waves.id"))
    checkpoint_pct = Column(Integer, nullable=False)  # 25, 50, 75, 100
    file_path = Column(String, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    wave = relationship("Wave", back_populates="uploads")
    check_results = relationship("CheckResult", back_populates="upload")


class QuotaRule(Base):
    """A single quota target, e.g. Male = 200, Female = 200, Age 18-24 = 100."""
    __tablename__ = "quota_rules"

    id = Column(Integer, primary_key=True, index=True)
    study_id = Column(Integer, ForeignKey("studies.id"))
    quota_group = Column(String, nullable=False)   # e.g. "Gender", "Age"
    quota_label = Column(String, nullable=False)   # e.g. "Male", "18-24"
    column_name = Column(String, nullable=False)   # matching column in uploaded data
    match_value = Column(String, nullable=False)   # value in that column that counts
    target_n = Column(Integer, nullable=False)      # required completes
    quota_type = Column(String, default="soft")     # soft | hard

    study = relationship("Study", back_populates="quota_rules")


class CheckResult(Base):
    """Stores the output of a QC run at a checkpoint, so history is preserved."""
    __tablename__ = "check_results"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, ForeignKey("data_uploads.id"))
    result_json = Column(JSON)          # full structured results
    ai_summary = Column(Text, nullable=True)
    ai_email_draft = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    upload = relationship("DataUpload", back_populates="check_results")
