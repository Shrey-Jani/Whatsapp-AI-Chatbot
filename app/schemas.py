from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str          # web client's local session key
    message: str


class ChatResponse(BaseModel):
    reply: str
    done: bool = False
    images: list[str] = []        # checklist card URLs shown alongside the reply


class SubmissionOut(BaseModel):
    id: int
    client_id: int
    status: str
    summary_pdf_url: str | None = None

    model_config = {"from_attributes": True}
