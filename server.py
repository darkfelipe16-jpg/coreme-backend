print("🚨 SERVER NOVO CARREGADO 🚨")
import os
import io
import json
import uuid
import jwt
import bcrypt
import zipfile
import logging
import base64

from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

import aiofiles
from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, HTTPException, Depends, UploadFile, File, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr
from openpyxl import Workbook

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get("DB_NAME", "coreme_uepa")]

JWT_SECRET = os.environ.get("JWT_SECRET", "uepa_coreme_secret_key_2025")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

UPLOAD_DIR = ROOT_DIR / "uploads" / "pdfs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="COREME UEPA - Sistema de Envio de PDF",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)
@app.api_route("/", methods=["GET", "HEAD"])
async def root_test():
    return {"ok": True, "message": "root funcionando"}

@app.get("/ping")
async def ping():
    return {"ping": "pong"}
api_router = APIRouter(prefix="/api")
security = HTTPBearer()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    program: str
    year: Optional[str] = None
    role: str = "resident"
    cpf: Optional[str] = None
    scenario: Optional[str] = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    program: str
    year: Optional[str] = None
    role: str
    cpf: Optional[str] = None
    scenario: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class User(UserBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    program: str
    year: Optional[str] = None
    role: str
    cpf: Optional[str] = None
    scenario: Optional[str] = None
    created_at: datetime
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class Submission(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    user_name: str
    user_email: str
    program: str
    year: str
    reference_month: str
    reference_month_name: str
    file_path: str
    file_name: str
    submitted_at: datetime = Field(default_factory=datetime.utcnow)


class SubmissionResponse(BaseModel):
    id: str
    user_id: str
    user_name: str
    user_email: str
    program: str
    year: str
    reference_month: str
    reference_month_name: str
    submitted_at: datetime
    status: str = "Enviado"


class SystemConfig(BaseModel):
    id: str = "system_config"
    logo_base64: Optional[str] = None
    system_name: str = "COREME UEPA"
    primary_color: str = "#1E3A8A"
    secondary_color: str = "#FFFFFF"
    login_image_base64: Optional[str] = None
    institutional_message: str = "Sistema de Envio Mensal de Documentos da Residência Médica"
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SystemConfigUpdate(BaseModel):
    logo_base64: Optional[str] = None
    system_name: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    login_image_base64: Optional[str] = None
    institutional_message: Optional[str] = None


MONTH_NAMES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
}


def get_drive_service():
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise Exception("Credenciais do Google não configuradas")

    info = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=credentials)


async def upload_to_drive(file_content: bytes, filename: str, mime_type: Optional[str]):
    service = get_drive_service()
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

    if not folder_id:
        raise Exception("GOOGLE_DRIVE_FOLDER_ID não configurado")

    file_stream = io.BytesIO(file_content)
    file_metadata = {
        "name": filename,
        "parents": [folder_id]
    }

    media = MediaIoBaseUpload(
        file_stream,
        mimetype=mime_type or "application/pdf",
        resumable=True
    )

    created_file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name"
    ).execute()

    return created_file


def download_from_drive(file_id: str) -> io.BytesIO:
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    buffer.seek(0)
    return buffer


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_token(user_id: str, role: str) -> str:
    expiration = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expiration
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    payload = decode_token(credentials.credentials)
    user = await db.users.find_one({"id": payload["sub"]})
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")
    return user


async def get_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores.")
    return current_user


def get_reference_month_info() -> dict:
    now = datetime.utcnow()
    current_day = now.day

    if now.month == 1:
        ref_year = now.year - 1
        ref_month = 12
    else:
        ref_year = now.year
        ref_month = now.month - 1

    reference_month = f"{ref_year}-{ref_month:02d}"
    reference_month_name = f"{MONTH_NAMES_PT[ref_month]} {ref_year}"

    deadline_day = 4
    is_within_deadline = current_day <= deadline_day

    if is_within_deadline:
        deadline = datetime(now.year, now.month, deadline_day, 23, 59, 59)
    else:
        deadline = None

    return {
        "reference_month": reference_month,
        "reference_month_name": reference_month_name,
        "deadline": deadline.isoformat() if deadline else None,
        "is_within_deadline": is_within_deadline,
        "current_day": current_day,
        "deadline_day": deadline_day,
        "message": "Prazo de envio encerrado." if not is_within_deadline else f"Prazo: até dia {deadline_day} às 23:59"
    }


def sanitize_filename(name: str) -> str:
    import re
    import unicodedata

    name = unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode("ASCII")
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s]+", "", name)
    return name


@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    existing = await db.users.find_one({"email": user_data.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    if user_data.role == "resident":
        if not user_data.year:
            raise HTTPException(status_code=400, detail="Year obrigatório para residente")

    elif user_data.role == "preceptor":
        # 👇 aqui decide se quer obrigar ou não
        if not user_data.cpf or not user_data.scenario:
            raise HTTPException(status_code=400, detail="CPF e cenário obrigatórios para preceptor")

    elif user_data.role == "admin":
        pass

    else:
        raise HTTPException(status_code=400, detail="Role inválido")

    user_dict = {
        "id": str(uuid.uuid4()),
        "email": user_data.email.lower(),
        "full_name": user_data.full_name,
        "program": user_data.program,
        "year": user_data.year,
        "password": hash_password(user_data.password),
        "role": user_data.role,
        "cpf": user_data.cpf,
        "scenario": user_data.scenario,
        "created_at": datetime.utcnow(),
        "is_active": True
    }

    await db.users.insert_one(user_dict)

    token = create_token(user_dict["id"], user_dict["role"])

    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user_dict["id"],
            email=user_dict["email"],
            full_name=user_dict["full_name"],
            program=user_dict["program"],
            year=user_dict.get("year"),
            role=user_dict["role"],
            cpf=user_dict.get("cpf"),
            scenario=user_dict.get("scenario"),
            created_at=user_dict["created_at"],
            is_active=user_dict["is_active"]
        )
    )


@api_router.post("/auth/login", response_model=TokenResponse)
async def login(credentials: UserLogin):
    user = await db.users.find_one({"email": credentials.email.lower()})
    if not user or not verify_password(credentials.password, user["password"]):
        raise HTTPException(status_code=401, detail="Email ou senha inválidos")

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Conta desativada")

    token = create_token(user["id"], user["role"])

    return TokenResponse(
    access_token=token,
    user=UserResponse(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        program=user["program"],
        year=user.get("year"),
        role=user["role"],
        cpf=user.get("cpf"),
        scenario=user.get("scenario"),
        created_at=user["created_at"],
        is_active=user["is_active"]
    )
)


@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
    id=current_user["id"],
    email=current_user["email"],
    full_name=current_user["full_name"],
    program=current_user.get("program", ""),
    year=current_user.get("year"),
    role=current_user.get("role", "resident"),
    cpf=current_user.get("cpf"),
    scenario=current_user.get("scenario"),
    created_at=current_user["created_at"],
    is_active=current_user.get("is_active", True)
)


@api_router.get("/submissions/deadline-info")
async def get_deadline_info():
    return get_reference_month_info()


@api_router.post("/submissions/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    deadline_info = get_reference_month_info()
    if not deadline_info["is_within_deadline"]:
        raise HTTPException(status_code=400, detail="Prazo de envio encerrado. O envio é permitido apenas do dia 1 ao dia 4 de cada mês.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Apenas arquivos PDF são aceitos")

    content = await file.read()

    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo muito grande. Máximo permitido: 10MB")

    reference_month = deadline_info["reference_month"]

    existing = await db.submissions.find_one({
        "user_id": current_user["id"],
        "reference_month": reference_month
    })
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Você já enviou um documento para {deadline_info['reference_month_name']}. Apenas 1 envio por mês é permitido."
        )

    sanitized_name = sanitize_filename(current_user["full_name"])
    month_name_sanitized = sanitize_filename(deadline_info["reference_month_name"])
    filename = f"{sanitized_name}_{month_name_sanitized}.pdf"

    created_file = await upload_to_drive(
        content,
        filename,
        file.content_type
    )
    file_path = created_file["id"]

    submission = Submission(
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        user_email=current_user["email"],
        program=current_user.get("program", ""),
        year=current_user.get("year", ""),
        reference_month=reference_month,
        reference_month_name=deadline_info["reference_month_name"],
        file_path=str(file_path),
        file_name=filename
    )

    await db.submissions.insert_one(submission.dict())

    logger.info(f"Submission created: {current_user['full_name']} - {deadline_info['reference_month_name']}")

    return {
        "message": f"PDF enviado com sucesso para {deadline_info['reference_month_name']}",
        "submission": SubmissionResponse(
            id=submission.id,
            user_id=submission.user_id,
            user_name=submission.user_name,
            user_email=submission.user_email,
            program=submission.program,
            year=submission.year,
            reference_month=submission.reference_month,
            reference_month_name=submission.reference_month_name,
            submitted_at=submission.submitted_at
        )
    }


@api_router.get("/submissions/my-history", response_model=List[SubmissionResponse])
async def get_my_submissions(current_user: dict = Depends(get_current_user)):
    submissions = await db.submissions.find(
        {"user_id": current_user["id"]}
    ).sort("reference_month", -1).to_list(1000)

    return [
        SubmissionResponse(
            id=s["id"],
            user_id=s["user_id"],
            user_name=s["user_name"],
            user_email=s["user_email"],
            program=s.get("program", ""),
            year=s.get("year", ""),
            reference_month=s["reference_month"],
            reference_month_name=s["reference_month_name"],
            submitted_at=s["submitted_at"]
        )
        for s in submissions
    ]


@api_router.get("/submissions/check/{reference_month}")
async def check_submission(reference_month: str, current_user: dict = Depends(get_current_user)):
    existing = await db.submissions.find_one({
        "user_id": current_user["id"],
        "reference_month": reference_month
    })
    return {"submitted": existing is not None}


@api_router.get("/admin/submissions", response_model=List[SubmissionResponse])
async def get_all_submissions(
    reference_month: Optional[str] = None,
    program: Optional[str] = None,
    year: Optional[str] = None,
    admin_user: dict = Depends(get_admin_user)
):
    query = {}

    if reference_month:
        query["reference_month"] = reference_month
    if program:
        query["program"] = program
    if year:
        query["year"] = year

    submissions = await db.submissions.find(query).sort("user_name", 1).to_list(10000)

    return [
        SubmissionResponse(
            id=s["id"],
            user_id=s["user_id"],
            user_name=s["user_name"],
            user_email=s["user_email"],
            program=s.get("program", ""),
            year=s.get("year", ""),
            reference_month=s["reference_month"],
            reference_month_name=s["reference_month_name"],
            submitted_at=s["submitted_at"]
        )
        for s in submissions
    ]


@api_router.get("/admin/users", response_model=List[UserResponse])
async def get_all_users(admin_user: dict = Depends(get_admin_user)):
    users = await db.users.find({"role": "resident"}).sort("full_name", 1).to_list(10000)

    return [
        UserResponse(
            id=u["id"],
            email=u["email"],
            full_name=u["full_name"],
            program=u.get("program", ""),
            year=u.get("year", ""),
            role=u["role"],
            created_at=u["created_at"],
            is_active=u.get("is_active", True)
        )
        for u in users
    ]


@api_router.get("/admin/programs")
async def get_programs(admin_user: dict = Depends(get_admin_user)):
    programs = await db.users.distinct("program")
    return {"programs": [p for p in programs if p]}


@api_router.get("/admin/reference-months")
async def get_reference_months(admin_user: dict = Depends(get_admin_user)):
    months = await db.submissions.distinct("reference_month")
    months_sorted = sorted(months, reverse=True)

    result = []
    for m in months_sorted:
        year, month_num = m.split("-")
        month_name = f"{MONTH_NAMES_PT[int(month_num)]} {year}"
        result.append({"value": m, "label": month_name})

    return {"months": result}


@api_router.get("/admin/download/{submission_id}")
async def download_submission(submission_id: str, admin_user: dict = Depends(get_admin_user)):
    submission = await db.submissions.find_one({"id": submission_id})
    if not submission:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    buffer = download_from_drive(submission["file_path"])

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={submission['file_name']}"}
    )


@api_router.get("/admin/export-excel")
async def export_excel(
    reference_month: Optional[str] = None,
    admin_user: dict = Depends(get_admin_user)
):
    query = {}
    if reference_month:
        query["reference_month"] = reference_month

    submissions = await db.submissions.find(query).sort("user_name", 1).to_list(10000)

    wb = Workbook()
    ws = wb.active
    ws.title = "Envios"

    headers = ["Nome", "Email", "Programa", "Ano", "Mês de Referência", "Status", "Data e Hora do Envio"]
    ws.append(headers)

    for s in submissions:
        ws.append([
            s["user_name"],
            s["user_email"],
            s.get("program", ""),
            s.get("year", ""),
            s["reference_month_name"],
            "Enviado",
            s["submitted_at"].strftime("%d/%m/%Y %H:%M:%S")
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"relatorio_envios_{reference_month or 'geral'}.xlsx"

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@api_router.get("/admin/export-zip")
async def export_zip(
    reference_month: str = Query(..., description="Reference month in format YYYY-MM"),
    admin_user: dict = Depends(get_admin_user)
):
    submissions = await db.submissions.find(
        {"reference_month": reference_month}
    ).sort("user_name", 1).to_list(10000)

    if not submissions:
        raise HTTPException(status_code=404, detail="Nenhum envio encontrado para este mês")

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in submissions:
            drive_buffer = download_from_drive(s["file_path"])
            sanitized_name = sanitize_filename(s["user_name"])
            month_name_sanitized = sanitize_filename(s["reference_month_name"])
            new_filename = f"{sanitized_name}_{month_name_sanitized}.pdf"
            zf.writestr(new_filename, drive_buffer.getvalue())

    buffer.seek(0)

    year, month_num = reference_month.split("-")
    month_name = f"{MONTH_NAMES_PT[int(month_num)]}_{year}"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=PDFs_{month_name}.zip"}
    )


@api_router.get("/config")
async def get_system_config():
    config = await db.system_config.find_one({"id": "system_config"})
    if not config:
        return SystemConfig().dict()
    return config


@api_router.put("/admin/config")
async def update_system_config(
    config_update: SystemConfigUpdate,
    admin_user: dict = Depends(get_admin_user)
):
    update_data = {k: v for k, v in config_update.dict().items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()

    await db.system_config.update_one(
        {"id": "system_config"},
        {"$set": update_data},
        upsert=True
    )

    config = await db.system_config.find_one({"id": "system_config"})
    return config


@api_router.get("/admin/stats")
async def get_stats(admin_user: dict = Depends(get_admin_user)):
    total_users = await db.users.count_documents({"role": "resident"})
    total_submissions = await db.submissions.count_documents({})

    deadline_info = get_reference_month_info()
    current_month_submissions = await db.submissions.count_documents({
        "reference_month": deadline_info["reference_month"]
    })

    pipeline = [
        {"$group": {"_id": "$program", "count": {"$sum": 1}}}
    ]
    by_program = await db.submissions.aggregate(pipeline).to_list(100)

    return {
        "total_users": total_users,
        "total_submissions": total_submissions,
        "current_month": deadline_info["reference_month_name"],
        "current_month_submissions": current_month_submissions,
        "is_within_deadline": deadline_info["is_within_deadline"],
        "by_program": by_program
    }


@api_router.get("/")
async def root():
    return {"message": "COREME UEPA API", "status": "online"}


@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_db_client():
    admin = await db.users.find_one({"email": "admin@uepa.br"})
    if not admin:
        admin_user = {
            "id": str(uuid.uuid4()),
            "email": "admin@uepa.br",
            "full_name": "Administrador COREME",
            "program": "Administração",
            "year": "N/A",
            "password": hash_password("admin123"),
            "role": "admin",
            "created_at": datetime.utcnow(),
            "is_active": True
        }
        await db.users.insert_one(admin_user)
        logger.info("Default admin user created: admin@uepa.br / admin123")

    await db.users.create_index("email", unique=True)
    await db.users.create_index("id")
    await db.submissions.create_index([("user_id", 1), ("reference_month", 1)])
    await db.submissions.create_index("reference_month")

    logger.info("COREME UEPA API started successfully")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
