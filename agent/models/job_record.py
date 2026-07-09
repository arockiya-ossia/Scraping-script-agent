from typing import Optional

from pydantic import BaseModel


class JobRecord(BaseModel):
    title: Optional[str] = None
    job_id: Optional[str] = None  # stable requisition ID

    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None

    url: Optional[str] = None
    apply_url: Optional[str] = None

    date_posted: Optional[str] = None  # typed/ISO parsed, via dateutil — never regex
    date_posted_text: Optional[str] = None  # raw fallback text

    job_description: Optional[str] = None  # full text, single field/selector

    employment_type: Optional[str] = None  # structural only — null if not exposed structurally
    work_type: Optional[str] = None
    salary_range: Optional[str] = None
