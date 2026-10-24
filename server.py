print("🚨 ARTHUR FELIPE F. MARTINS 🚨")
import cloudinary
import cloudinary.uploader
import os
import io
import json
import uuid
import jwt
import bcrypt
import zipfile
import requests
import logging
import base64
import numpy as np
import cv2
import pytesseract
import os

pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

from pdf2image import convert_from_bytes
from openpyxl.styles import PatternFill

from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

import aiofiles
from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, HTTPException, Depends, UploadFile, File, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError, OperationFailure, ServerSelectionTimeoutError
from pydantic import BaseModel, Field, EmailStr
from openpyxl import Workbook

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

print("CLOUDINARY_URL exists:", bool(os.getenv("CLOUDINARY_URL")))

print("Cloudinary config:", cloudinary.config().cloud_name)

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=10000)
db = client[os.environ.get("DB_NAME", "coreme_uepa")]

JWT_SECRET = os.environ["JWT_SECRET"]
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
from fastapi.middleware.cors import CORSMiddleware

# CORS - libera o frontend local e pode liberar o domínio publicado via variável de ambiente.
# No Render, você pode criar a variável FRONTEND_URL com o link do frontend publicado.
frontend_url = os.getenv("FRONTEND_URL", "").strip()

allowed_origins = [
    "http://localhost:8081",
    "http://127.0.0.1:8081",
    "http://localhost:8082",
    "http://127.0.0.1:8082",
    "http://localhost:8083",
    "http://127.0.0.1:8083",
    "http://localhost:19006",
    "http://127.0.0.1:19006",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

if frontend_url:
    allowed_origins.append(frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    coreme: Optional[str] = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    program: str
    year: Optional[str] = None
    role: str
    cpf: Optional[str] = None
    scenario: Optional[str] = None
    coreme: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str

class CoremeCreate(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None
    email: EmailStr
    password: str

class CoremeConfirmationAtestoResponse(BaseModel):
    id: str
    reference_month: str
    atesto_file_path: Optional[str] = None
    atesto_file_name: Optional[str] = None
    confirmed_at: Optional[datetime] = None

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
    coreme: Optional[str] = None
    created_at: datetime
    is_active: bool
    identity_status: str = "not_started"


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

    aulas_file_path: Optional[str] = None
    aulas_file_name: Optional[str] = None

    orientacao_file_path: Optional[str] = None
    orientacao_file_name: Optional[str] = None

    submitted_at: datetime = Field(default_factory=datetime.utcnow)


class SubmissionResponse(BaseModel):
    id: str
    user_id: str
    user_name: str
    user_email: str
    program: str
    year: Optional[str] = None
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
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=401, detail="Usuário não encontrado ou inativo")
    return user


async def get_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas administradores.")
    return current_user

async def get_coreme_user(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "coreme":
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas COREME.")
    return current_user


def get_submission_deadline(reference_month: str) -> datetime:
    year, month = [int(part) for part in reference_month.split("-")]
    if month == 12:
        deadline_year = year + 1
        deadline_month = 1
    else:
        deadline_year = year
        deadline_month = month + 1

    return datetime(deadline_year, deadline_month, 4, 23, 59, 59)


def get_delivery_status(submitted_at: Optional[datetime], reference_month: str) -> str:
    if not submitted_at:
        return "not_delivered"

    deadline = get_submission_deadline(reference_month)
    return "delivered_on_time" if submitted_at <= deadline else "delivered_late"


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
    
    FORCE_UPLOAD_OPEN = True
    is_within_deadline = FORCE_UPLOAD_OPEN or (current_day <= deadline_day)

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
    name = re.sub(r"[\s]+", "_", name)
    return name.lower()


def get_user_type_folder(user: dict) -> str:
    role = user.get("role", "").lower()
    if role == "preceptor":
        return "preceptores"
    return "residentes"


def generate_frequencia_folder(user: dict, reference_month: str) -> str:
    user_type = get_user_type_folder(user)
    return f"coreme/{user_type}/frequencia/{reference_month}"


def generate_user_material_folder(user: dict, reference_month: str) -> str:
    user_type = get_user_type_folder(user)
    program = sanitize_filename(user.get("program", "sem_programa"))
    full_name = sanitize_filename(user.get("full_name", "sem_nome"))
    return f"coreme/{user_type}/{program}/{full_name}/{reference_month}"


def upload_to_cloudinary(file_content: bytes, filename: str, folder: str, public_id: str):
    print("Enviando para Cloudinary...")

    result = cloudinary.uploader.upload(
        file_content,
        resource_type="raw",
        folder=folder,
        public_id=public_id,
        display_name=filename,
        overwrite=True
    )

    print("Upload concluído")
    return result


TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

MONTH_TOKENS_PT = {
    1: ["janeiro", "jan"],
    2: ["fevereiro", "fev"],
    3: ["marco", "março", "mar"],
    4: ["abril", "abr"],
    5: ["maio", "mai"],
    6: ["junho", "jun"],
    7: ["julho", "jul"],
    8: ["agosto", "ago"],
    9: ["setembro", "set"],
    10: ["outubro", "out"],
    11: ["novembro", "nov"],
    12: ["dezembro", "dez"],
}


def normalize_ocr_text(text: str) -> str:
    if not text:
        return ""

    text = text.lower()
    text = text.replace("ç", "c")
    text = text.replace("á", "a").replace("à", "a").replace("ã", "a").replace("â", "a")
    text = text.replace("é", "e").replace("ê", "e")
    text = text.replace("í", "i")
    text = text.replace("ó", "o").replace("ô", "o").replace("õ", "o")
    text = text.replace("ú", "u")
    return text


def month_found_in_text(text: str, expected_month: int) -> bool:
    clean = normalize_ocr_text(text)
    tokens = MONTH_TOKENS_PT.get(expected_month, [])
    return any(token in clean for token in tokens)


def pil_to_cv2(pil_image):
    img = np.array(pil_image)
    if len(img.shape) == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def crop_relative_region(img, box):
    """
    box = (x1, y1, x2, y2) em proporção da imagem
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box

    x1 = max(0, min(w, int(x1 * w)))
    x2 = max(0, min(w, int(x2 * w)))
    y1 = max(0, min(h, int(y1 * h)))
    y2 = max(0, min(h, int(y2 * h)))

    return img[y1:y2, x1:x2]


def detect_handwritten_mark(region_img, min_pixels=250):
    if region_img is None or region_img.size == 0:
        return False, 0

    if len(region_img.shape) == 3:
        gray = cv2.cvtColor(region_img, cv2.COLOR_BGR2GRAY)
    else:
        gray = region_img.copy()

    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

    kernel = np.ones((2, 2), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    pixels = cv2.countNonZero(thresh)
    return pixels >= min_pixels, int(pixels)


def analyze_frequency_pdf(pdf_bytes: bytes, reference_month_name: str):
    """
    Analisa apenas a FREQUÊNCIA:
    - tenta localizar o mês esperado no OCR
    - verifica se há marca gráfica na área do residente
    - verifica se há marca gráfica na área do preceptor
    """

    result = {
        "status": "ok",
        "month_ok": True,
        "resident_signature_ok": True,
        "preceptor_signature_ok": True,
        "issues": [],
        "ocr_excerpt": "",
        "resident_pixels": 0,
        "preceptor_pixels": 0,
    }

    try:
        pages = convert_from_bytes(pdf_bytes, dpi=200, first_page=1, last_page=1)
    except Exception as e:
        result["status"] = "pendente"
        result["issues"].append(f"Falha ao converter PDF: {str(e)}")
        return result

    if not pages:
        result["status"] = "pendente"
        result["issues"].append("PDF sem páginas legíveis")
        return result

    first_page = pages[0]
    text = pytesseract.image_to_string(first_page, lang="por")
    result["ocr_excerpt"] = (text or "")[:500]

    # mês esperado vem de "Março 2026", por exemplo
    expected_month_name = normalize_ocr_text(reference_month_name.split()[0])
    if expected_month_name not in normalize_ocr_text(text):
        result["month_ok"] = False
        result["issues"].append("Mês do documento não confere com a competência esperada")

    img_cv = pil_to_cv2(first_page)

    # áreas iniciais de teste - provavelmente vamos ajustar depois
    resident_box = (0.05, 0.72, 0.48, 0.97)
    preceptor_box = (0.52, 0.72, 0.97, 0.97)

    resident_region = crop_relative_region(img_cv, resident_box)
    preceptor_region = crop_relative_region(img_cv, preceptor_box)

    resident_ok, resident_pixels = detect_handwritten_mark(resident_region)
    preceptor_ok, preceptor_pixels = detect_handwritten_mark(preceptor_region)

    result["resident_signature_ok"] = resident_ok
    result["preceptor_signature_ok"] = preceptor_ok
    result["resident_pixels"] = resident_pixels
    result["preceptor_pixels"] = preceptor_pixels

    if not resident_ok:
        result["issues"].append("Campo de assinatura/rubrica do residente aparenta estar vazio")

    if not preceptor_ok:
        result["issues"].append("Campo de assinatura/rubrica do preceptor aparenta estar vazio")

    if result["issues"]:
        result["status"] = "pendente"

    return result

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    existing = await db.users.find_one({"email": user_data.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    if user_data.role == "resident":
        if not user_data.year:
            raise HTTPException(status_code=400, detail="Year obrigatório para residente")
        if not user_data.coreme:
            raise HTTPException(status_code=400, detail="COREME obrigatória para residente")

    elif user_data.role == "preceptor":
        # 👇 aqui decide se quer obrigar ou não
        if not user_data.cpf or not user_data.scenario:
            raise HTTPException(status_code=400, detail="CPF e cenário obrigatórios para preceptor")
        if not user_data.coreme:
            raise HTTPException(status_code=400, detail="COREME obrigatória para preceptor")

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
        "coreme": user_data.coreme,
        "created_at": datetime.utcnow(),
        "is_active": True,
        "identity_status": "not_started"
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
            coreme=user_dict.get("coreme"),
            created_at=user_dict["created_at"],
            is_active=user_dict["is_active"],
            identity_status=user_dict.get("identity_status", "not_started")
        )
    )


@api_router.post("/auth/login", response_model=TokenResponse)
async def login(credentials: UserLogin):
    try:
        user = await db.users.find_one({"email": credentials.email.lower()})
    except PyMongoError as exc:
        logger.exception("Falha de banco durante autenticacao")
        raise HTTPException(status_code=503, detail="Banco de dados indisponivel. Verifique a configuracao do MongoDB Atlas.") from exc
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
        coreme=user.get("coreme"),
        created_at=user["created_at"],
        is_active=user["is_active"],
        identity_status=user.get("identity_status", "not_started")
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
    coreme=current_user.get("coreme"),
    created_at=current_user["created_at"],
    is_active=current_user.get("is_active", True),
    identity_status=current_user.get("identity_status", "not_started")
)


@api_router.get("/submissions/deadline-info")
async def get_deadline_info():
    return get_reference_month_info()

@api_router.post("/submissions/upload")
async def upload_pdf(
    file: Optional[UploadFile] = File(None),
    aulas_ministradas_file: Optional[UploadFile] = File(None),
    orientacao_trabalho_file: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user)
):
    try:
        if current_user.get("role") not in ("resident", "preceptor"):
            raise HTTPException(status_code=403, detail="Envio exclusivo para residentes e preceptores")
        # Validação no servidor: não confiar em bloqueio visual do aplicativo.
        if current_user.get("identity_status") != "verified":
            raise HTTPException(status_code=403, detail="Identidade ainda não verificada. Envio de frequência bloqueado")

        print("=== INICIO UPLOAD ===")
        print("file:", file.filename if file else None)
        print("aulas:", aulas_ministradas_file.filename if aulas_ministradas_file else None)
        print("orientacao:", orientacao_trabalho_file.filename if orientacao_trabalho_file else None)
        print("usuario:", current_user.get("email"))

        if not file or not aulas_ministradas_file or not orientacao_trabalho_file:
            raise HTTPException(
                status_code=400,
                detail="Todos os arquivos são obrigatórios"
            )

        deadline_info = get_reference_month_info()
        print("deadline_info:", deadline_info)

        files_to_validate = [
            ("Frequência", file),
            ("Aulas Ministradas", aulas_ministradas_file),
            ("Orientação de Trabalho", orientacao_trabalho_file),
        ]

        validated_files = {}

        for label, current_file in files_to_validate:
            print("Processando:", label, current_file.filename)

            if not current_file.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail=f"{label}: apenas arquivos PDF são aceitos"
                )

            content = await current_file.read()
            print(f"{label} tamanho:", len(content))

            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(
                    status_code=400,
                    detail=f"{label}: arquivo muito grande. Máximo permitido: 10MB"
                )

            validated_files[label] = content

        print("Iniciando OCR da frequência")
        ocr_analysis = analyze_frequency_pdf(
            validated_files["Frequência"],
            deadline_info["reference_month_name"]
        )
        print("OCR resultado:", ocr_analysis)

        sanitized_name = sanitize_filename(current_user["full_name"])
        sanitized_program = sanitize_filename(current_user.get("program", "sem_programa"))
        month_name_sanitized = sanitize_filename(deadline_info["reference_month_name"])

        frequencia_folder = generate_frequencia_folder(
            current_user,
            deadline_info["reference_month"]
        )

        material_folder = generate_user_material_folder(
            current_user,
            deadline_info["reference_month"]
        )

        frequencia_filename = f"{sanitized_name}_{sanitized_program}_{month_name_sanitized}_frequencia.pdf"
        aulas_filename = f"{sanitized_name}_{month_name_sanitized}_aulas.pdf"
        orientacao_filename = f"{sanitized_name}_{month_name_sanitized}_orientacao.pdf"

        print("Iniciando upload Cloudinary da frequência")
        frequencia_cloud = upload_to_cloudinary(
            validated_files["Frequência"],
            frequencia_filename,
            frequencia_folder,
            f"{sanitized_name}_{sanitized_program}_frequencia"
        )
        print("Upload frequência OK")

        aulas_cloud = upload_to_cloudinary(
            validated_files["Aulas Ministradas"],
            aulas_filename,
            material_folder,
            "aulas"
        )
        print("Upload aulas OK")

        orientacao_cloud = upload_to_cloudinary(
            validated_files["Orientação de Trabalho"],
            orientacao_filename,
            material_folder,
            "orientacao"
        )
        print("Upload orientação OK")

        submission = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "user_name": current_user["full_name"],
            "user_email": current_user["email"],
            "program": current_user.get("program", ""),
            "year": current_user.get("year") or "",
            "reference_month": deadline_info["reference_month"],
            "reference_month_name": deadline_info["reference_month_name"],
            "submitted_at": datetime.utcnow(),

            "file_path": frequencia_cloud["secure_url"],
            "file_name": frequencia_filename,

            "aulas_file_path": aulas_cloud["secure_url"],
            "aulas_file_name": aulas_filename,

            "orientacao_file_path": orientacao_cloud["secure_url"],
            "orientacao_file_name": orientacao_filename,

            "ocr_status": ocr_analysis["status"],
            "ocr_issues": ocr_analysis["issues"],
            "ocr_analysis": ocr_analysis,
        }

        print("Salvando submission no Mongo")
        await db.submissions.insert_one(submission)
        print("Submission salva com sucesso")
        print("=== FIM UPLOAD ===")

        return {
            "message": "Upload realizado com sucesso",
            "ocr_status": ocr_analysis["status"],
            "ocr_issues": ocr_analysis["issues"],
            "ocr_analysis": ocr_analysis
        }

    except HTTPException:
        raise

    except Exception as e:
        print("ERRO GERAL NO UPLOAD:", str(e))
        logger.exception("ERRO GERAL NO UPLOAD")
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno no upload: {str(e)}"
        )

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
            year=s.get("year") or "",
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

    if not reference_month:
        reference_month = get_reference_month_info()["reference_month"]

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
            cpf=u.get("cpf"),
            scenario=u.get("scenario"),
            coreme=u.get("coreme"),
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

    current_reference = get_reference_month_info()["reference_month"]
    if current_reference not in months:
        months.append(current_reference)

    months_sorted = sorted(months, reverse=True)

    result = []
    for m in months_sorted:
        year, month_num = m.split("-")
        month_name = f"{MONTH_NAMES_PT[int(month_num)]} {year}"
        result.append({"value": m, "label": month_name})

    return {"months": result, "current_reference_month": current_reference}


@api_router.get("/admin/download/{submission_id}")
async def download_submission(submission_id: str, admin_user: dict = Depends(get_admin_user)):
    submission = await db.submissions.find_one({"id": submission_id})
    if not submission:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    response = requests.get(submission["file_path"])
    buffer = io.BytesIO(response.content)

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

    red_fill = PatternFill(fill_type="solid", fgColor="FFC7CE")

    for s in submissions:
        status_excel = "Pendente OCR" if s.get("ocr_status") == "pendente" else "Enviado"

        ws.append([
            s["user_name"],
            s["user_email"],
            s.get("program", ""),
            s.get("year", ""),
            s["reference_month_name"],
            status_excel,
            s["submitted_at"].strftime("%d/%m/%Y %H:%M:%S")
        ])

        current_row = ws.max_row

        if s.get("ocr_status") == "pendente":
            for col in range(1, 8):
                ws.cell(row=current_row, column=col).fill = red_fill

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
            sanitized_name = sanitize_filename(s["user_name"])
            month_name_sanitized = sanitize_filename(s["reference_month_name"])
            new_filename = f"{sanitized_name}_{month_name_sanitized}.pdf"

            response = requests.get(s["file_path"])
            zf.writestr(new_filename, response.content)

    buffer.seek(0)

    year, month_num = reference_month.split("-")
    month_name = f"{MONTH_NAMES_PT[int(month_num)]}_{year}"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=PDFs_{month_name}.zip"}
    )
@api_router.get("/admin/export-zip-aulas")
async def export_zip_aulas(
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
            if not s.get("aulas_file_path"):
                continue

            response = requests.get(s["aulas_file_path"])
            sanitized_name = sanitize_filename(s["user_name"])
            month_name_sanitized = sanitize_filename(s["reference_month_name"])
            new_filename = f"{sanitized_name}_{month_name_sanitized}_aulas.pdf"
            zf.writestr(new_filename, response.content)

    buffer.seek(0)

    year, month_num = reference_month.split("-")
    month_name = f"{MONTH_NAMES_PT[int(month_num)]}_{year}"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=Aulas_{month_name}.zip"}
    )

@api_router.get("/admin/export-zip-orientacao")
async def export_zip_orientacao(
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
            if not s.get("orientacao_file_path"):
                continue

            sanitized_name = sanitize_filename(s["user_name"])
            month_name_sanitized = sanitize_filename(s["reference_month_name"])
            new_filename = f"{sanitized_name}_{month_name_sanitized}_orientacao.pdf"

            response = requests.get(s["orientacao_file_path"])
            zf.writestr(new_filename, response.content)

    buffer.seek(0)

    year, month_num = reference_month.split("-")
    month_name = f"{MONTH_NAMES_PT[int(month_num)]}_{year}"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=Orientacao_{month_name}.zip"}
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
    total_preceptors = await db.users.count_documents({"role": "preceptor"})
    total_submissions = await db.submissions.count_documents({})

    preceptors = await db.users.find({"role": "preceptor"}).to_list(None)
    preceptor_ids = [p["id"] for p in preceptors]

    total_preceptor_submissions = await db.submissions.count_documents({
        "user_id": {"$in": preceptor_ids}
    })

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
        "total_preceptors": total_preceptors,
        "total_submissions": total_submissions,
        "total_preceptor_submissions": total_preceptor_submissions,
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



@api_router.post("/admin/coremes")
async def create_coreme(
    coreme_data: CoremeCreate,
    admin_user: dict = Depends(get_admin_user)
):
    coreme_name = (coreme_data.name or coreme_data.full_name or "").strip()

    if not coreme_name:
        raise HTTPException(status_code=400, detail="Nome da COREME é obrigatório")

    if len(coreme_data.password) < 6:
        raise HTTPException(status_code=400, detail="A senha deve ter pelo menos 6 caracteres")

    existing = await db.users.find_one({"email": coreme_data.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="E-mail já cadastrado")

    coreme_user = {
        "id": str(uuid.uuid4()),
        "email": coreme_data.email.lower(),
        "full_name": coreme_name,
        "program": "COREME",
        "year": "N/A",
        "password": hash_password(coreme_data.password),
        "role": "coreme",
        "cpf": None,
        "scenario": None,
        "created_at": datetime.utcnow(),
        "is_active": True,
    }

    await db.users.insert_one(coreme_user)

    return {
        "message": "Perfil COREME criado com sucesso",
        "coreme": {
            "id": coreme_user["id"],
            "name": coreme_user["full_name"],
            "email": coreme_user["email"],
            "role": coreme_user["role"],
            "is_active": coreme_user["is_active"],
        }
    }


@api_router.get("/admin/coremes")
async def list_coremes(admin_user: dict = Depends(get_admin_user)):
    coremes = await db.users.find({"role": "coreme"}).sort("full_name", 1).to_list(10000)

    return [
        {
            "id": c["id"],
            "name": c.get("full_name", ""),
            "email": c.get("email", ""),
            "role": c.get("role", "coreme"),
            "created_at": c.get("created_at"),
            "is_active": c.get("is_active", True),
        }
        for c in coremes
    ]



@api_router.get("/coreme/dashboard")
async def get_coreme_dashboard(
    reference_month: Optional[str] = None,
    coreme_user: dict = Depends(get_coreme_user)
):
    deadline_info = get_reference_month_info()
    if not reference_month:
        reference_month = deadline_info["reference_month"]

    year, month_num = reference_month.split("-")
    reference_month_name = f"{MONTH_NAMES_PT[int(month_num)]} {year}"

    coreme_name = coreme_user.get("full_name", "")

    linked_users = await db.users.find({
        "role": {"$in": ["resident", "preceptor"]},
        "coreme": coreme_name,
        "is_active": True
    }).sort("full_name", 1).to_list(10000)

    linked_ids = [u["id"] for u in linked_users]

    submissions = await db.submissions.find({
        "reference_month": reference_month,
        "user_id": {"$in": linked_ids}
    }).to_list(10000)

    submissions_by_user = {s["user_id"]: s for s in submissions}

    people = []
    delivered_on_time = 0
    delivered_late = 0
    not_delivered = 0

    for u in linked_users:
        submission = submissions_by_user.get(u["id"])
        submitted_at = submission.get("submitted_at") if submission else None
        status = get_delivery_status(submitted_at, reference_month)

        if status == "delivered_on_time":
            delivered_on_time += 1
        elif status == "delivered_late":
            delivered_late += 1
        else:
            not_delivered += 1

        people.append({
            "id": u["id"],
            "full_name": u.get("full_name", ""),
            "email": u.get("email", ""),
            "role": u.get("role", ""),
            "program": u.get("program", ""),
            "year": u.get("year"),
            "scenario": u.get("scenario"),
            "submitted_at": submitted_at.isoformat() if submitted_at else None,
            "status": status,
        })

    reviews = await db.coreme_reviews.find({
        "coreme_id": coreme_user["id"], "reference_month": reference_month
    }).to_list(10000)
    reviews_by_user = {r["beneficiary_id"]: r for r in reviews}
    for person in people:
        review = reviews_by_user.get(person["id"])
        person["review_decision"] = review.get("decision") if review else None
        person["review_reason"] = review.get("reason") if review else None
        person["reviewed_at"] = review["reviewed_at"].isoformat() if review and review.get("reviewed_at") else None

    confirmation = await db.coreme_confirmations.find_one({
        "coreme_id": coreme_user["id"],
        "reference_month": reference_month
    })

    return {
        "reference_month": reference_month,
        "reference_month_name": reference_month_name,
        "coreme_name": coreme_name,
        "confirmation_status": "confirmed" if confirmation else "pending",
        "confirmed_at": confirmation.get("confirmed_at").isoformat() if confirmation and confirmation.get("confirmed_at") else None,
        "atesto_file_path": confirmation.get("atesto_file_path") if confirmation else None,
        "atesto_file_name": confirmation.get("atesto_file_name") if confirmation else None,
        "total_linked": len(linked_users),
        "delivered_on_time": delivered_on_time,
        "delivered_late": delivered_late,
        "not_delivered": not_delivered,
        "people": people,
    }


class CoremeReviewRequest(BaseModel):
    reference_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    beneficiary_id: str
    decision: str
    reason: Optional[str] = None


@api_router.post("/coreme/reviews")
async def review_monthly_frequency(
    payload: CoremeReviewRequest,
    coreme_user: dict = Depends(get_coreme_user)
):
    """Conferência interna da COREME; não equivale à decisão da Comissão."""
    if await db.commission_batches.find_one({"coreme_id": coreme_user["id"], "reference_month": payload.reference_month}):
        raise HTTPException(status_code=409, detail="Competência já encaminhada à Comissão; revisão bloqueada")
    if payload.decision not in ("conforme", "pendente", "nao_conforme"):
        raise HTTPException(status_code=400, detail="Decisão inválida")
    reason = (payload.reason or "").strip()
    if payload.decision != "conforme" and len(reason) < 5:
        raise HTTPException(status_code=400, detail="Informe o motivo da pendência ou não conformidade")
    beneficiary = await db.users.find_one({
        "id": payload.beneficiary_id,
        "role": {"$in": ["resident", "preceptor"]},
        "coreme": coreme_user.get("full_name"),
        "is_active": True
    })
    if not beneficiary:
        raise HTTPException(status_code=404, detail="Beneficiário não vinculado a esta COREME")
    submission = await db.submissions.find_one({
        "user_id": payload.beneficiary_id,
        "reference_month": payload.reference_month
    })
    if not submission:
        raise HTTPException(status_code=400, detail="Não há frequência enviada nesta competência")
    now = datetime.utcnow()
    record = {
        "coreme_id": coreme_user["id"],
        "beneficiary_id": payload.beneficiary_id,
        "reference_month": payload.reference_month,
        "decision": payload.decision,
        "reason": reason if payload.decision != "conforme" else "",
        "reviewed_by": coreme_user["id"],
        "reviewed_at": now,
        "submission_id": submission.get("id"),
    }
    key = {k: record[k] for k in ("coreme_id", "beneficiary_id", "reference_month")}
    previous = await db.coreme_reviews.find_one(key)
    await db.coreme_reviews.update_one(key, {"$set": record}, upsert=True)
    await db.audit_events.insert_one({
        "id": str(uuid.uuid4()), "action": "coreme_frequency_review",
        "actor_id": coreme_user["id"], "beneficiary_id": payload.beneficiary_id,
        "reference_month": payload.reference_month,
        "previous_decision": previous.get("decision") if previous else None,
        "decision": payload.decision, "reason": record["reason"], "at": now
    })
    return {"ok": True, "decision": payload.decision, "reviewed_at": now.isoformat()}


@api_router.post("/coreme/monthly-confirmation")
async def confirm_coreme_month(
    reference_month: str = Query(...),
    atesto_file: UploadFile = File(...),
    coreme_user: dict = Depends(get_coreme_user)
):
    if await db.commission_batches.find_one({"coreme_id": coreme_user["id"], "reference_month": reference_month}):
        raise HTTPException(status_code=409, detail="Competência já encaminhada à Comissão")
    if not atesto_file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="O atesto precisa ser enviado em PDF")

    content = await atesto_file.read()

    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo muito grande. Máximo permitido: 10MB")

    dashboard = await get_coreme_dashboard(reference_month=reference_month, coreme_user=coreme_user)

    if dashboard["delivered_on_time"] == 0:
        raise HTTPException(
            status_code=400,
            detail="Não há residentes ou preceptores com entrega dentro do prazo para atestar neste mês"
        )

    sanitized_coreme = sanitize_filename(coreme_user.get("full_name", "coreme"))
    sanitized_month = sanitize_filename(reference_month)
    atesto_filename = f"{sanitized_coreme}_{sanitized_month}_atesto_coreme.pdf"

    atesto_cloud = upload_to_cloudinary(
        content,
        atesto_filename,
        f"coreme/atestados/{sanitized_coreme}/{reference_month}",
        f"{sanitized_coreme}_{sanitized_month}_atesto"
    )

    confirmation = {
        "id": str(uuid.uuid4()),
        "coreme_id": coreme_user["id"],
        "coreme_name": coreme_user.get("full_name", ""),
        "reference_month": reference_month,
        "confirmed_at": datetime.utcnow(),
        "confirmed_by": coreme_user.get("email", ""),
        "atesto_file_path": atesto_cloud["secure_url"],
        "atesto_file_name": atesto_filename,
        "delivered_on_time": dashboard["delivered_on_time"],
        "delivered_late": dashboard["delivered_late"],
        "not_delivered": dashboard["not_delivered"],
        "total_linked": dashboard["total_linked"],
    }

    await db.coreme_confirmations.update_one(
        {
            "coreme_id": coreme_user["id"],
            "reference_month": reference_month
        },
        {"$set": confirmation},
        upsert=True
    )

    return await get_coreme_dashboard(reference_month=reference_month, coreme_user=coreme_user)


@api_router.get("/coreme/atesto")
async def download_coreme_atesto(
    reference_month: str = Query(...),
    coreme_user: dict = Depends(get_coreme_user)
):
    confirmation = await db.coreme_confirmations.find_one({
        "coreme_id": coreme_user["id"],
        "reference_month": reference_month
    })

    if not confirmation or not confirmation.get("atesto_file_path"):
        raise HTTPException(status_code=404, detail="Atesto não encontrado para este mês")

    response = requests.get(confirmation["atesto_file_path"])
    buffer = io.BytesIO(response.content)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={confirmation.get('atesto_file_name', 'atesto_coreme.pdf')}"}
    )


@api_router.get("/coreme/report")
async def export_coreme_report(
    reference_month: Optional[str] = None,
    coreme_user: dict = Depends(get_coreme_user)
):
    dashboard = await get_coreme_dashboard(reference_month=reference_month, coreme_user=coreme_user)

    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório COREME"

    ws.append(["COREME", dashboard["coreme_name"]])
    ws.append(["Mês de referência", dashboard["reference_month_name"]])
    ws.append(["Total vinculados", dashboard["total_linked"]])
    ws.append(["Dentro do prazo", dashboard["delivered_on_time"]])
    ws.append(["Fora do prazo", dashboard["delivered_late"]])
    ws.append(["Não entregaram", dashboard["not_delivered"]])
    ws.append([])

    headers = ["Nome", "E-mail", "Perfil", "Programa", "Ano", "Cenário", "Status", "Data de envio"]
    ws.append(headers)

    status_label = {
        "delivered_on_time": "Entregou dentro do prazo",
        "delivered_late": "Entregou fora do prazo",
        "not_delivered": "Não entregou",
    }

    for person in dashboard["people"]:
        submitted_at = person.get("submitted_at")
        submitted_text = ""
        if submitted_at:
            try:
                submitted_text = datetime.fromisoformat(submitted_at).strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                submitted_text = submitted_at

        ws.append([
            person.get("full_name", ""),
            person.get("email", ""),
            "Residente" if person.get("role") == "resident" else "Preceptor",
            person.get("program", ""),
            person.get("year") or "",
            person.get("scenario") or "",
            status_label.get(person.get("status"), person.get("status")),
            submitted_text,
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"relatorio_coreme_{sanitize_filename(dashboard['coreme_name'])}_{dashboard['reference_month']}.xlsx"

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ETAPA 2 — Encaminhamento institucional e análise da Comissão.
# O envio à Comissão é explícito: uma confirmação/atesto não significa encaminhamento.
class CommissionAccountCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str

class CommissionReviewRequest(BaseModel):
    reference_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    coreme_id: str
    beneficiary_id: str
    decision: str
    reason: Optional[str] = None

async def get_commission_user(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") not in ("commission", "admin"):
        raise HTTPException(status_code=403, detail="Acesso restrito à Comissão")
    return current_user

@api_router.post("/admin/commission-users")
async def create_commission_user(payload: CommissionAccountCreate, admin: dict = Depends(get_admin_user)):
    if len(payload.password) < 10:
        raise HTTPException(status_code=400, detail="Senha deve conter ao menos 10 caracteres")
    if await db.users.find_one({"email": payload.email.lower()}):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado")
    record = {"id": str(uuid.uuid4()), "email": payload.email.lower(),
              "full_name": payload.full_name.strip(), "program": "Comissão QualificaSaúde",
              "year": None, "password": hash_password(payload.password),
              "role": "commission", "is_active": True, "created_at": datetime.utcnow()}
    await db.users.insert_one(record)
    return {"id": record["id"], "email": record["email"], "role": "commission"}

@api_router.post("/coreme/forward-to-commission")
async def forward_to_commission(reference_month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                                coreme_user: dict = Depends(get_coreme_user)):
    key = {"coreme_id": coreme_user["id"], "reference_month": reference_month}
    if await db.commission_batches.find_one(key):
        raise HTTPException(status_code=409, detail="Competência já encaminhada; solicite reabertura administrativa")
    confirmation = await db.coreme_confirmations.find_one(key)
    if not confirmation:
        raise HTTPException(status_code=400, detail="É necessário confirmar a competência e enviar o atesto antes")
    reviews = await db.coreme_reviews.find({**key, "decision": "conforme"}).to_list(10000)
    beneficiaries = []
    for r in reviews:
        user = await db.users.find_one({"id": r["beneficiary_id"], "coreme": coreme_user.get("full_name"),
                                        "role": {"$in": ["resident", "preceptor"]}, "is_active": True})
        submission = await db.submissions.find_one({"user_id": r["beneficiary_id"],
                                                     "reference_month": reference_month})
        if user and submission and submission.get("id") == r.get("submission_id"):
            beneficiaries.append({"beneficiary_id": user["id"], "beneficiary_name": user.get("full_name"),
                                  "role": user.get("role"), "program": user.get("program"),
                                  "submission_id": submission["id"]})
    if not beneficiaries:
        raise HTTPException(status_code=400, detail="Nenhuma frequência conforme para encaminhamento")
    now = datetime.utcnow()
    batch = {**key, "id": str(uuid.uuid4()), "coreme_name": coreme_user.get("full_name"),
             "beneficiaries": beneficiaries, "atesto_file_path": confirmation.get("atesto_file_path"),
             "forwarded_at": now, "forwarded_by": coreme_user["id"], "status": "em_analise"}
    await db.commission_batches.insert_one(batch)
    await db.audit_events.insert_one({"id": str(uuid.uuid4()), "action": "forward_to_commission",
                                       "actor_id": coreme_user["id"], "reference_month": reference_month,
                                       "batch_id": batch["id"], "count": len(beneficiaries), "at": now})
    return {"ok": True, "batch_id": batch["id"], "forwarded_count": len(beneficiaries)}

@api_router.get("/commission/dashboard")
async def commission_dashboard(reference_month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                               actor: dict = Depends(get_commission_user)):
    batches = await db.commission_batches.find({"reference_month": reference_month}).to_list(10000)
    reviews = await db.commission_reviews.find({"reference_month": reference_month}).to_list(20000)
    review_map = {(r["coreme_id"], r["beneficiary_id"]): r for r in reviews}
    output = []
    for batch in batches:
        people = []
        for person in batch["beneficiaries"]:
            r = review_map.get((batch["coreme_id"], person["beneficiary_id"]))
            people.append({**person, "decision": r.get("decision") if r else None,
                           "reason": r.get("reason") if r else None,
                           "reviewed_at": r["reviewed_at"].isoformat() if r else None})
        output.append({"id": batch["id"], "coreme_id": batch["coreme_id"],
                       "coreme_name": batch["coreme_name"], "status": batch.get("status"),
                       "forwarded_at": batch["forwarded_at"].isoformat(),
                       "atesto_file_path": batch.get("atesto_file_path"), "beneficiaries": people})
    return {"reference_month": reference_month, "batches": output,
            "total_forwarded": sum(len(b["beneficiaries"]) for b in output),
            "total_reviewed": sum(1 for b in output for x in b["beneficiaries"] if x["decision"]),
            "total_conforme": sum(1 for b in output for x in b["beneficiaries"] if x["decision"] == "conforme")}

@api_router.post("/commission/reviews")
async def commission_review(payload: CommissionReviewRequest,
                            actor: dict = Depends(get_commission_user)):
    if payload.decision not in ("conforme", "nao_conforme"):
        raise HTTPException(status_code=400, detail="Decisão inválida")
    reason = (payload.reason or "").strip()
    if payload.decision == "nao_conforme" and len(reason) < 5:
        raise HTTPException(status_code=400, detail="Justificativa obrigatória para não conformidade")
    batch = await db.commission_batches.find_one({"coreme_id": payload.coreme_id,
                                                   "reference_month": payload.reference_month})
    if not batch or not any(p["beneficiary_id"] == payload.beneficiary_id for p in batch["beneficiaries"]):
        raise HTTPException(status_code=404, detail="Frequência não encaminhada à Comissão")
    if batch.get("status") != "em_analise":
        raise HTTPException(status_code=409, detail="Lote encerrado para análise")
    if await db.payment_lists.find_one({"reference_month": payload.reference_month}):
        raise HTTPException(status_code=409, detail="Relação de pagamento consolidada; análise bloqueada")
    key = {"coreme_id": payload.coreme_id, "reference_month": payload.reference_month,
           "beneficiary_id": payload.beneficiary_id}
    old = await db.commission_reviews.find_one(key)
    now = datetime.utcnow()
    await db.commission_reviews.update_one(key, {"$set": {**key, "decision": payload.decision,
        "reason": reason if payload.decision == "nao_conforme" else "",
        "reviewed_by": actor["id"], "reviewed_at": now}}, upsert=True)
    await db.audit_events.insert_one({"id": str(uuid.uuid4()), "action": "commission_review",
        "actor_id": actor["id"], "beneficiary_id": payload.beneficiary_id,
        "coreme_id": payload.coreme_id, "reference_month": payload.reference_month,
        "previous_decision": old.get("decision") if old else None,
        "decision": payload.decision, "reason": reason, "at": now})
    return {"ok": True, "decision": payload.decision}

# ETAPA 3 — Consolidação e encaminhamento à SESPA/SAGA.
# O sistema gera relação administrativa; não executa transferência bancária.
class SespaAccountCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str


async def get_finance_user(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") not in ("sespa", "commission", "admin"):
        raise HTTPException(status_code=403, detail="Acesso restrito à Comissão e SESPA/SAGA")
    return current_user


@api_router.post("/admin/sespa-users")
async def create_sespa_user(payload: SespaAccountCreate, admin: dict = Depends(get_admin_user)):
    if len(payload.password) < 10:
        raise HTTPException(status_code=400, detail="Senha deve conter ao menos 10 caracteres")
    if await db.users.find_one({"email": payload.email.lower()}):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado")
    record = {"id": str(uuid.uuid4()), "email": payload.email.lower(),
              "full_name": payload.full_name.strip(), "program": "SESPA/SAGA", "year": None,
              "password": hash_password(payload.password), "role": "sespa",
              "is_active": True, "created_at": datetime.utcnow()}
    await db.users.insert_one(record)
    return {"id": record["id"], "email": record["email"], "role": "sespa"}


def payment_snapshot_public(doc: dict) -> dict:
    return {"id": doc["id"], "reference_month": doc["reference_month"],
            "status": doc["status"], "generated_at": doc["generated_at"].isoformat(),
            "generated_by": doc["generated_by"], "forwarded_at": doc.get("forwarded_at").isoformat() if doc.get("forwarded_at") else None,
            "forwarded_by": doc.get("forwarded_by"), "total": len(doc["beneficiaries"]),
            "beneficiaries": doc["beneficiaries"]}


async def build_payment_candidates(reference_month: str) -> tuple[list, list]:
    batches = await db.commission_batches.find({"reference_month": reference_month}).to_list(10000)
    candidates = []
    missing = []
    seen = set()
    for batch in batches:
        for person in batch["beneficiaries"]:
            key = {"coreme_id": batch["coreme_id"], "reference_month": reference_month,
                   "beneficiary_id": person["beneficiary_id"]}
            review = await db.commission_reviews.find_one(key)
            if not review:
                missing.append({"coreme": batch.get("coreme_name"), "beneficiary": person.get("beneficiary_name")})
                continue
            if review["decision"] != "conforme":
                continue
            if person["beneficiary_id"] in seen:
                raise HTTPException(status_code=409, detail="Beneficiário consta em mais de um lote; corrigir duplicidade antes de consolidar")
            seen.add(person["beneficiary_id"])
            candidates.append({"beneficiary_id": person["beneficiary_id"],
                               "beneficiary_name": person.get("beneficiary_name"),
                               "role": person.get("role"), "program": person.get("program"),
                               "coreme_id": batch["coreme_id"], "coreme_name": batch.get("coreme_name"),
                               "submission_id": person.get("submission_id")})
    candidates.sort(key=lambda x: (x["coreme_name"] or "", x["beneficiary_name"] or ""))
    return candidates, missing



# ETAPA 4 — Ocorrências e declarações de elegibilidade (execução, sem seleção por edital)
OCCURRENCE_TYPES = {"ferias", "afastamento", "suspensao", "desligamento", "beneficio_incompativel", "pagamento_indevido", "outros"}
OCCURRENCE_STATES = {"registrada", "em_analise", "confirmada", "descartada"}

class OccurrenceCreate(BaseModel):
    beneficiary_id: str
    reference_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    occurrence_type: str
    description: str = Field(min_length=8, max_length=2000)
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class OccurrenceDecision(BaseModel):
    status: str
    reason: str = Field(min_length=8, max_length=2000)

class BenefitDeclaration(BaseModel):
    reference_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    declares_no_incompatible_benefits: bool


def occurrence_public(item: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in item.items() if k != "_id"}

async def occurrence_access(actor: dict, beneficiary: dict) -> bool:
    role = actor.get("role")
    if role in {"admin", "commission", "sespa"}:
        return True
    if role == "coreme":
        return beneficiary.get("coreme") in {actor.get("full_name"), actor.get("name"), actor.get("coreme")}
    return actor.get("id") == beneficiary.get("id")

@api_router.get("/occurrences")
async def list_occurrences(reference_month: Optional[str] = None, beneficiary_id: Optional[str] = None,
                           actor: dict = Depends(get_current_user)):
    query = {}
    if reference_month:
        if not __import__('re').fullmatch(r"\d{4}-(0[1-9]|1[0-2])", reference_month):
            raise HTTPException(status_code=422, detail="Competência inválida")
        query["reference_month"] = reference_month
    if beneficiary_id:
        query["beneficiary_id"] = beneficiary_id
    if actor["role"] in {"resident", "preceptor"}:
        query["beneficiary_id"] = actor["id"]
    items = await db.occurrences.find(query).sort("created_at", -1).to_list(1000)
    if actor["role"] == "coreme":
        items = [i for i in items if await occurrence_access(actor, await db.users.find_one({"id": i["beneficiary_id"]}) or {})]
    return [occurrence_public(i) for i in items]

@api_router.post("/occurrences")
async def create_occurrence(payload: OccurrenceCreate, actor: dict = Depends(get_current_user)):
    beneficiary = await db.users.find_one({"id": payload.beneficiary_id})
    if not beneficiary or beneficiary.get("role") not in {"resident", "preceptor"}:
        raise HTTPException(status_code=404, detail="Beneficiário não encontrado")
    if not await occurrence_access(actor, beneficiary):
        raise HTTPException(status_code=403, detail="Sem acesso ao beneficiário")
    if actor["role"] == "sespa":
        raise HTTPException(status_code=403, detail="Perfil SESPA apenas consulta ocorrências")
    if payload.occurrence_type not in OCCURRENCE_TYPES:
        raise HTTPException(status_code=422, detail="Tipo de ocorrência inválido")
    if await db.payment_lists.find_one({"reference_month": payload.reference_month}):
        raise HTTPException(status_code=409, detail="Relação já consolidada; ocorrência exige procedimento administrativo separado")
    now = datetime.utcnow()
    doc = {"id": str(uuid.uuid4()), **payload.model_dump(), "beneficiary_name": beneficiary.get("full_name"),
           "coreme": beneficiary.get("coreme"), "status": "registrada", "created_at": now,
           "created_by": actor["id"], "decided_at": None, "decided_by": None, "decision_reason": None}
    await db.occurrences.insert_one(doc)
    await db.audit_events.insert_one({"id": str(uuid.uuid4()), "action": "occurrence_created", "actor_id": actor["id"],
        "occurrence_id": doc["id"], "reference_month": payload.reference_month, "created_at": now})
    return occurrence_public(doc)

@api_router.post("/occurrences/{occurrence_id}/decision")
async def decide_occurrence(occurrence_id: str, payload: OccurrenceDecision, actor: dict = Depends(get_current_user)):
    if actor.get("role") not in {"commission", "admin"}:
        raise HTTPException(status_code=403, detail="Decisão restrita à Comissão")
    if payload.status not in {"confirmada", "descartada", "em_analise"}:
        raise HTTPException(status_code=422, detail="Situação inválida")
    doc = await db.occurrences.find_one({"id": occurrence_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Ocorrência não encontrada")
    if await db.payment_lists.find_one({"reference_month": doc["reference_month"]}):
        raise HTTPException(status_code=409, detail="Relação consolidada; decisão bloqueada")
    now = datetime.utcnow()
    await db.occurrences.update_one({"id": occurrence_id}, {"$set": {"status": payload.status,
        "decision_reason": payload.reason, "decided_at": now, "decided_by": actor["id"]}})
    await db.audit_events.insert_one({"id": str(uuid.uuid4()), "action": "occurrence_decided", "actor_id": actor["id"],
        "occurrence_id": occurrence_id, "status": payload.status, "created_at": now})
    return {"ok": True, "status": payload.status}

@api_router.post("/benefits/declaration")
async def save_benefit_declaration(payload: BenefitDeclaration, actor: dict = Depends(get_current_user)):
    if actor.get("role") not in {"resident", "preceptor"}:
        raise HTTPException(status_code=403, detail="Declaração exclusiva do beneficiário")
    if await db.payment_lists.find_one({"reference_month": payload.reference_month}):
        raise HTTPException(status_code=409, detail="Competência já consolidada")
    now = datetime.utcnow()
    await db.benefit_declarations.update_one({"beneficiary_id": actor["id"], "reference_month": payload.reference_month},
        {"$set": {"declares_no_incompatible_benefits": payload.declares_no_incompatible_benefits,
        "updated_at": now, "actor_id": actor["id"]}}, upsert=True)
    await db.audit_events.insert_one({"id": str(uuid.uuid4()), "action": "benefit_declaration", "actor_id": actor["id"],
        "reference_month": payload.reference_month, "created_at": now})
    return {"ok": True, "reference_month": payload.reference_month}

@api_router.get("/benefits/declaration")
async def get_benefit_declaration(reference_month: str, actor: dict = Depends(get_current_user)):
    if actor.get("role") not in {"resident", "preceptor"}:
        raise HTTPException(status_code=403, detail="Consulta exclusiva do beneficiário")
    doc = await db.benefit_declarations.find_one({"beneficiary_id": actor["id"], "reference_month": reference_month})
    return {"reference_month": reference_month, "declares_no_incompatible_benefits":
            doc.get("declares_no_incompatible_benefits") if doc else None}

@api_router.get("/commission/payment-preview")
async def payment_preview(reference_month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                          actor: dict = Depends(get_commission_user)):
    candidates, missing = await build_payment_candidates(reference_month)
    existing = await db.payment_lists.find_one({"reference_month": reference_month})
    return {"reference_month": reference_month, "total_eligible": len(candidates),
            "pending_reviews": missing, "beneficiaries": candidates,
            "existing_list": payment_snapshot_public(existing) if existing else None}


@api_router.post("/commission/payment-lists")
async def create_payment_list(reference_month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                              actor: dict = Depends(get_commission_user)):
    if await db.payment_lists.find_one({"reference_month": reference_month}):
        raise HTTPException(status_code=409, detail="Já existe relação consolidada nesta competência")
    candidates, missing = await build_payment_candidates(reference_month)
    blockers = await db.occurrences.find({"reference_month": reference_month,
        "status": {"$in": ["registrada", "em_analise", "confirmada"]},
        "occurrence_type": {"$in": ["suspensao", "desligamento", "beneficio_incompativel", "pagamento_indevido"]}}).to_list(10000)
    affected = {o["beneficiary_id"] for o in blockers}
    if any(p["beneficiary_id"] in affected for p in candidates):
        raise HTTPException(status_code=409, detail="Há ocorrências impeditivas ou pendentes de análise; revise antes de consolidar")
    declarations = await db.benefit_declarations.find({"reference_month": reference_month,
        "declares_no_incompatible_benefits": False}).to_list(10000)
    if any(p["beneficiary_id"] in {d["beneficiary_id"] for d in declarations} for p in candidates):
        raise HTTPException(status_code=409, detail="Beneficiário declarou benefício potencialmente incompatível; análise necessária")
    if missing:
        raise HTTPException(status_code=409, detail=f"Existem {len(missing)} frequência(s) sem decisão da Comissão")
    if not candidates:
        raise HTTPException(status_code=400, detail="Nenhum beneficiário conforme para consolidar")
    now = datetime.utcnow()
    doc = {"id": str(uuid.uuid4()), "reference_month": reference_month,
           "beneficiaries": candidates, "generated_at": now, "generated_by": actor["id"],
           "status": "rascunho", "forwarded_at": None, "forwarded_by": None}
    try:
        await db.payment_lists.insert_one(doc)
    except Exception as e:
        if "duplicate" in str(e).lower() or "E11000" in str(e):
            raise HTTPException(status_code=409, detail="Relação já criada por outro usuário")
        raise
    await db.audit_events.insert_one({"id": str(uuid.uuid4()), "action": "payment_list_generated",
        "actor_id": actor["id"], "reference_month": reference_month,
        "count": len(candidates), "at": now})
    return payment_snapshot_public(doc)


@api_router.post("/commission/payment-lists/{reference_month}/forward")
async def forward_payment_list(reference_month: str, actor: dict = Depends(get_commission_user)):
    if not __import__('re').fullmatch(r"\d{4}-(0[1-9]|1[0-2])", reference_month):
        raise HTTPException(status_code=422, detail="Competência inválida")
    now = datetime.utcnow()
    result = await db.payment_lists.update_one({"reference_month": reference_month, "status": "rascunho"},
        {"$set": {"status": "encaminhada_sespa", "forwarded_at": now, "forwarded_by": actor["id"]}})
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Relação inexistente ou já encaminhada")
    await db.audit_events.insert_one({"id": str(uuid.uuid4()), "action": "payment_list_forwarded",
        "actor_id": actor["id"], "reference_month": reference_month, "at": now})
    return {"ok": True, "status": "encaminhada_sespa", "forwarded_at": now.isoformat(),
            "note": "Registro de encaminhamento no sistema; não substitui protocolo externo obrigatório"}


@api_router.get("/payments/lists")
async def list_payment_lists(reference_month: Optional[str] = None,
                             actor: dict = Depends(get_finance_user)):
    if reference_month and not __import__('re').fullmatch(r"\d{4}-(0[1-9]|1[0-2])", reference_month):
        raise HTTPException(status_code=422, detail="Competência inválida")
    query = {"reference_month": reference_month} if reference_month else {}
    if actor["role"] == "sespa":
        query["status"] = "encaminhada_sespa"
    docs = await db.payment_lists.find(query).sort("reference_month", -1).to_list(500)
    return [payment_snapshot_public(d) for d in docs]


@api_router.get("/payments/lists/{reference_month}/xlsx")
async def export_payment_list(reference_month: str, actor: dict = Depends(get_finance_user)):
    if not __import__('re').fullmatch(r"\d{4}-(0[1-9]|1[0-2])", reference_month):
        raise HTTPException(status_code=422, detail="Competência inválida")
    doc = await db.payment_lists.find_one({"reference_month": reference_month})
    if not doc or (actor["role"] == "sespa" and doc["status"] != "encaminhada_sespa"):
        raise HTTPException(status_code=404, detail="Relação não disponível")
    wb = Workbook()
    ws = wb.active
    ws.title = "Beneficiários aptos"
    ws.append(["Competência", "Nome", "Tipo", "Programa", "COREME/COREMU", "ID beneficiário"])
    for person in doc["beneficiaries"]:
        ws.append([reference_month, person.get("beneficiary_name"),
                   "Residente" if person.get("role") == "resident" else "Preceptor",
                   person.get("program"), person.get("coreme_name"), person.get("beneficiary_id")])
    for col, width in {"A": 15, "B": 40, "C": 16, "D": 36, "E": 32, "F": 42}.items():
        ws.column_dimensions[col].width = width
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="qualificasaude_aptos_{reference_month}.xlsx"'})



# ETAPA 5 — Indicadores e relatórios de execução (somente usuários institucionais).
def _validate_competence(value: str) -> str:
    import re
    if not re.fullmatch(r"\\d{4}-(0[1-9]|1[0-2])", value):
        raise HTTPException(status_code=422, detail="Competência inválida; use AAAA-MM")
    return value

def _execution_scope(actor: dict) -> dict:
    if actor.get("role") not in {"coreme", "commission", "sespa", "admin"}:
        raise HTTPException(status_code=403, detail="Acesso restrito a perfis institucionais")
    return {"coreme": actor.get("full_name")} if actor.get("role") == "coreme" else {}

@api_router.get("/reports/overview")
async def execution_overview(
    reference_month: str = Query(...),
    actor: dict = Depends(get_current_user)
):
    _validate_competence(reference_month)
    scope = _execution_scope(actor)
    users = await db.users.find({"role": {"$in": ["resident", "preceptor"]}, "is_active": True, **scope},
                                {"_id": 0, "id": 1, "full_name": 1, "program": 1, "role": 1, "coreme": 1}).to_list(15000)
    ids = [u["id"] for u in users]
    submissions = await db.submissions.find({"reference_month": reference_month, "user_id": {"$in": ids}},
                                             {"_id": 0, "user_id": 1, "submitted_at": 1}).to_list(30000)
    submitted_ids = {s["user_id"] for s in submissions}
    review_query = {"reference_month": reference_month, "beneficiary_id": {"$in": ids}}
    coreme_reviews = await db.coreme_reviews.find(review_query, {"_id": 0, "beneficiary_id": 1, "decision": 1}).to_list(30000)
    commission_reviews = await db.commission_reviews.find(review_query, {"_id": 0, "beneficiary_id": 1, "decision": 1}).to_list(30000)
    batches_query = {"reference_month": reference_month}
    if actor.get("role") == "coreme":
        batches_query["coreme_id"] = actor["id"]
    batches = await db.commission_batches.find(batches_query, {"_id": 0, "beneficiaries": 1, "coreme_name": 1}).to_list(5000)
    forwarded_ids = {p["beneficiary_id"] for b in batches for p in b.get("beneficiaries", []) if p["beneficiary_id"] in ids}
    occurrences = await db.occurrences.find(review_query, {"_id": 0, "status": 1}).to_list(30000)
    payment = await db.payment_lists.find_one({"reference_month": reference_month}, {"_id": 0, "status": 1, "beneficiaries": 1})
    paid_ids = {p["beneficiary_id"] for p in payment.get("beneficiaries", [])} if payment else set()
    by_institution = {}
    by_program = {}
    for u in users:
        institution = u.get("coreme") or "Não informada"
        program = u.get("program") or "Não informado"
        for bucket, key in ((by_institution, institution), (by_program, program)):
            record = bucket.setdefault(key, {"total": 0, "enviadas": 0, "pendentes": 0})
            record["total"] += 1
            record["enviadas"] += int(u["id"] in submitted_ids)
            record["pendentes"] += int(u["id"] not in submitted_ids)
    def rows(bucket, key):
        return [{key: k, **v} for k, v in sorted(bucket.items())]
    return {
        "reference_month": reference_month,
        "total_beneficiaries": len(users),
        "submitted": len(submitted_ids),
        "not_submitted": len(users) - len(submitted_ids),
        "coreme_reviewed": len({r["beneficiary_id"] for r in coreme_reviews}),
        "coreme_conforme": len({r["beneficiary_id"] for r in coreme_reviews if r.get("decision") == "conforme"}),
        "forwarded_to_commission": len(forwarded_ids),
        "commission_reviewed": len({r["beneficiary_id"] for r in commission_reviews}),
        "commission_conforme": len({r["beneficiary_id"] for r in commission_reviews if r.get("decision") == "conforme"}),
        "occurrences_open": sum(1 for o in occurrences if o.get("status") in ("registrada", "em_analise")),
        "payment_list_status": payment.get("status") if payment else None,
        "payment_list_count": len(paid_ids & set(ids)),
        "by_institution": rows(by_institution, "institution"),
        "by_program": rows(by_program, "program"),
        "alerts": _execution_alerts(reference_month, len(users) - len(submitted_ids),
                                    len(forwarded_ids), len({r["beneficiary_id"] for r in commission_reviews}),
                                    payment, actor),
    }

def _execution_alerts(reference_month, missing, forwarded, reviewed, payment, actor):
    # Prazos legais se referem ao mês seguinte à competência.
    from datetime import date
    y, m = map(int, reference_month.split("-"))
    next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
    today = date.today()
    day5, day15 = date(next_y, next_m, 5), date(next_y, next_m, 15)
    alerts = []
    if missing:
        alerts.append({"level": "warning", "message": f"{missing} beneficiário(s) sem envio registrado na competência."})
    if actor.get("role") in ("coreme", "admin", "commission") and today <= day5:
        alerts.append({"level": "info", "message": f"Prazo de encaminhamento COREME/COREMU: {day5:%d/%m/%Y} (via UEPA)."})
    if actor.get("role") in ("commission", "admin"):
        if forwarded > reviewed:
            alerts.append({"level": "warning", "message": f"{forwarded-reviewed} frequência(s) encaminhada(s) ainda sem análise da Comissão."})
        if not payment and today <= day15:
            alerts.append({"level": "info", "message": f"Prazo de envio da relação à SESPA/SAGA: {day15:%d/%m/%Y}."})
    if payment and payment.get("status") == "rascunho":
        alerts.append({"level": "warning", "message": "Relação de pagamento gerada, mas ainda não encaminhada."})
    return alerts

@api_router.get("/reports/overview/xlsx")
async def export_execution_overview(
    reference_month: str = Query(...),
    actor: dict = Depends(get_current_user)
):
    result = await execution_overview(reference_month, actor)
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    ws.append(["Indicador", "Valor"])
    for label, key in [("Competência", "reference_month"), ("Beneficiários ativos", "total_beneficiaries"),
                       ("Frequências enviadas", "submitted"), ("Sem frequência", "not_submitted"),
                       ("Conferidas COREME", "coreme_reviewed"), ("Conformes COREME", "coreme_conforme"),
                       ("Encaminhadas à Comissão", "forwarded_to_commission"),
                       ("Analisadas Comissão", "commission_reviewed"), ("Conformes Comissão", "commission_conforme"),
                       ("Ocorrências em aberto", "occurrences_open"), ("Na relação de pagamento", "payment_list_count"),
                       ("Situação da relação", "payment_list_status")]:
        ws.append([label, result.get(key)])
    for sheet_name, field, label in [("Instituições", "by_institution", "institution"), ("Programas", "by_program", "program")]:
        sheet = wb.create_sheet(sheet_name)
        sheet.append([label.title(), "Total", "Enviadas", "Pendentes"])
        for row in result[field]:
            sheet.append([row[label], row["total"], row["enviadas"], row["pendentes"]])
        sheet.column_dimensions["A"].width = 45
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 24
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="qualificasaude_relatorio_{reference_month}.xlsx"'})


# ETAPA 6: Administração de usuários e consulta de auditoria.
# A administração de privilégios permanece exclusiva do administrador.
from fastapi import Request

ALLOWED_INSTITUTIONAL_ROLES = {"coreme", "commission", "sespa", "admin"}
MANAGED_ROLES = {"resident", "preceptor", "coreme", "commission", "sespa"}

class UserAdministrationUpdate(BaseModel):
    is_active: Optional[bool] = None
    coreme: Optional[str] = None

class InstitutionalAccountCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    role: str
    coreme: Optional[str] = None

async def append_security_audit(actor: dict, action: str, target_id: str,
                                previous: dict, changes: dict, request: Request):
    await db.audit_events.insert_one({
        "id": str(uuid.uuid4()), "action": action, "actor_id": actor["id"],
        "actor_role": actor.get("role"), "target_id": target_id,
        "previous": previous, "changes": changes, "at": datetime.utcnow(),
        "ip": request.client.host if request.client else None,
    })

@api_router.get("/security/users")
async def list_managed_users(
    role: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    actor: dict = Depends(get_admin_user)
):
    if role and role not in MANAGED_ROLES | {"admin"}:
        raise HTTPException(status_code=422, detail="Perfil inválido")
    query = {"role": role} if role else {}
    cursor = db.users.find(query, {"_id": 0, "password": 0, "cpf": 0}).sort("full_name", 1).skip(offset).limit(limit)
    return {"items": await cursor.to_list(limit), "total": await db.users.count_documents(query)}

@api_router.post("/security/users", status_code=201)
async def create_institutional_account(
    payload: InstitutionalAccountCreate,
    request: Request,
    actor: dict = Depends(get_admin_user)
):
    if payload.role not in {"coreme", "commission", "sespa"}:
        raise HTTPException(status_code=422, detail="Perfil institucional não permitido")
    name = payload.full_name.strip()
    if len(name) < 3 or len(payload.password) < 10:
        raise HTTPException(status_code=422, detail="Nome ou senha inválidos (mínimo 10 caracteres)")
    if payload.role == "coreme" and not payload.coreme:
        raise HTTPException(status_code=422, detail="Instituição COREME/COREMU obrigatória")
    email = str(payload.email).lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado")
    doc = {"id": str(uuid.uuid4()), "email": email, "full_name": name,
           "password": hash_password(payload.password), "role": payload.role,
           "coreme": payload.coreme.strip() if payload.coreme else None,
           "program": "Administração institucional", "year": None,
           "is_active": True, "created_at": datetime.utcnow()}
    await db.users.insert_one(doc)
    await append_security_audit(actor, "institutional_user_created", doc["id"], {},
                                {"role": doc["role"], "coreme": doc["coreme"]}, request)
    return {"id": doc["id"], "email": email, "role": doc["role"]}

@api_router.patch("/security/users/{user_id}")
async def update_managed_user(
    user_id: str,
    payload: UserAdministrationUpdate,
    request: Request,
    actor: dict = Depends(get_admin_user)
):
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Nenhuma alteração informada")
    if target["id"] == actor["id"] and changes.get("is_active") is False:
        raise HTTPException(status_code=409, detail="Não é permitido desativar a própria conta")
    if "coreme" in changes:
        if target.get("role") not in {"resident", "preceptor", "coreme"}:
            raise HTTPException(status_code=422, detail="Vínculo institucional não aplicável a este perfil")
        if not isinstance(changes["coreme"], str) or not changes["coreme"].strip():
            raise HTTPException(status_code=422, detail="Instituição não pode ficar vazia")
        changes["coreme"] = changes["coreme"].strip()
    before = {k: target.get(k) for k in changes}
    await db.users.update_one({"id": user_id}, {"$set": changes})
    await append_security_audit(actor, "user_updated", user_id, before, changes, request)
    return {"ok": True, "id": user_id, "changes": changes}

@api_router.get("/security/audit")
async def list_security_audit(
    action: Optional[str] = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=300),
    offset: int = Query(0, ge=0),
    actor: dict = Depends(get_admin_user)
):
    query = {"action": action} if action else {}
    # Excluir campos sensíveis eventualmente registrados por versões antigas.
    projection = {"_id": 0, "password": 0, "cpf": 0, "token": 0, "access_token": 0}
    cursor = db.audit_events.find(query, projection).sort("at", -1).skip(offset).limit(limit)
    entries = await cursor.to_list(limit)
    # Limitar saída aos metadados essenciais; evitar divulgação de justificativas clínicas.
    keys = {"id", "action", "actor_id", "actor_role", "target_id", "beneficiary_id",
            "reference_month", "at", "ip", "decision", "previous_decision"}
    return {"items": [{k: v for k, v in entry.items() if k in keys} for entry in entries],
            "total": await db.audit_events.count_documents(query)}

@api_router.get("/coremes")
async def public_list_coremes():
    coremes = await db.users.find({
        "role": "coreme",
        "is_active": True
    }).sort("full_name", 1).to_list(10000)

    return [
        {
            "id": c["id"],
            "name": c.get("full_name", ""),
            "email": c.get("email", ""),
        }
        for c in coremes
    ]

"""Rota experimental do ResiQ: comparação facial sem persistência.
Inserir este conteúdo em servidor.py antes de app.include_router(api_router).
Não utilizar como validação definitiva de identidade.
"""
from fastapi import UploadFile, File, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

FACE_MAX_BYTES = 5 * 1024 * 1024


# OpenCV YuNet + SFace, sem importar DeepFace.
# Os modelos são baixados na primeira utilização e mantidos em /tmp durante a vida do contêiner.
FACE_MODEL_DIR = Path(os.getenv("FACE_MODEL_DIR", "/tmp/resiq-face-models"))
FACE_DETECTOR_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
FACE_RECOGNIZER_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx"
)


def _face_model_path(filename: str, url: str, min_size: int) -> str:
    from urllib.request import urlopen
    FACE_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    target = FACE_MODEL_DIR / filename
    if target.exists() and target.stat().st_size >= min_size:
        return str(target)
    temporary = target.with_suffix(".download")
    try:
        with urlopen(url, timeout=40) as response, open(temporary, "wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        if temporary.stat().st_size < min_size:
            raise RuntimeError("Modelo facial incompleto")
        temporary.replace(target)
        logger.info("Modelo facial preparado: %s", filename)
        return str(target)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_face_images(document_image: bytes, selfie_image: bytes) -> dict:
    import cv2
    import numpy as np

    # Limitar o paralelismo para reduzir o pico de RAM no Render Free.
    cv2.setNumThreads(1)
    detector_path = _face_model_path(
        "face_detection_yunet_2023mar.onnx", FACE_DETECTOR_URL, 100_000
    )
    recognizer_path = _face_model_path(
        "face_recognition_sface_2021dec.onnx", FACE_RECOGNIZER_URL, 10_000_000
    )
    detector = cv2.FaceDetectorYN.create(detector_path, "", (320, 320), 0.7, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")

    def extract_feature(image_bytes: bytes, label: str):
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Imagem inválida")
        h, w = image.shape[:2]
        if h < 40 or w < 40:
            raise ValueError("Imagem muito pequena")
        # Redimensionamento antes da detecção evita grandes picos de memória.
        scale = min(1.0, 960.0 / max(h, w))
        if scale < 1:
            image = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))))
        height, width = image.shape[:2]
        detector.setInputSize((width, height))
        _, faces = detector.detect(image)
        if faces is None or len(faces) == 0:
            raise ValueError(f"Rosto não encontrado na imagem: {label}")
        if len(faces) != 1:
            raise ValueError(f"A imagem deve conter somente um rosto: {label}")
        aligned = recognizer.alignCrop(image, faces[0])
        return recognizer.feature(aligned)

    doc_feature = extract_feature(document_image, "documento")
    selfie_feature = extract_feature(selfie_image, "selfie")
    cosine_similarity = float(recognizer.match(
        doc_feature, selfie_feature, cv2.FaceRecognizerSF_FR_COSINE
    ))
    # Referência do exemplo oficial OpenCV SFace (não calibrado para este público).
    threshold = 0.363
    return {
        "match": cosine_similarity >= threshold,
        "distance": float(1.0 - cosine_similarity),
        "threshold": float(1.0 - threshold),
        "similarity": cosine_similarity,
        "status": "manual_review_required",
        "warning": "Resultado experimental; exige revisão humana. Não comprova documento autêntico nem prova de vida.",
    }


@api_router.get("/identity/status")
async def get_identity_status(current_user: dict = Depends(get_current_user)):
    status = current_user.get("identity_status", "not_started")
    return {"status": status, "verified": status == "verified"}


class IdentityReviewRequest(BaseModel):
    beneficiary_id: str
    decision: str
    reason: Optional[str] = None


@api_router.get("/coreme/identity/pending")
async def coreme_identity_pending(coreme_user: dict = Depends(get_coreme_user)):
    return await db.users.find({
        "role": {"$in": ["resident", "preceptor"]},
        "coreme": coreme_user.get("full_name"),
        "identity_status": "manual_review_required",
        "is_active": True,
    }, {"_id": 0, "id": 1, "full_name": 1, "role": 1, "program": 1,
        "identity_status": 1, "identity_comparison": 1}).to_list(1000)


@api_router.post("/coreme/identity/review")
async def coreme_review_identity(
    payload: IdentityReviewRequest,
    coreme_user: dict = Depends(get_coreme_user),
):
    if payload.decision not in ("approve", "retry"):
        raise HTTPException(status_code=400, detail="Decisão inválida")
    reason = (payload.reason or "").strip()
    if len(reason) < 15:
        raise HTTPException(status_code=400, detail="Registre a justificativa e a conferência independente realizada (mínimo 15 caracteres)")
    beneficiary = await db.users.find_one({
        "id": payload.beneficiary_id,
        "role": {"$in": ["resident", "preceptor"]},
        "coreme": coreme_user.get("full_name"),
        "is_active": True,
        "identity_status": "manual_review_required",
    })
    if not beneficiary:
        raise HTTPException(status_code=404, detail="Verificação pendente não encontrada nesta COREME")
    comparison = beneficiary.get("identity_comparison") or {}
    if payload.decision == "approve" and comparison.get("match") is not True:
        raise HTTPException(status_code=409, detail="Comparação sem correspondência. Solicite nova tentativa")
    now = datetime.utcnow()
    new_status = "verified" if payload.decision == "approve" else "not_started"
    result = await db.users.update_one(
        {"id": beneficiary["id"], "identity_status": "manual_review_required"},
        ({"$set": {"identity_status": new_status,
                   "identity_reviewed_at": now,
                   "identity_reviewed_by": coreme_user["id"]},
          "$unset": {"identity_comparison": ""}} if new_status == "not_started"
         else {"$set": {"identity_status": new_status,
                        "identity_reviewed_at": now,
                        "identity_reviewed_by": coreme_user["id"]}}),
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Estado alterado; atualize a página")
    await db.audit_events.insert_one({
        "id": str(uuid.uuid4()), "action": "identity_review",
        "actor_id": coreme_user["id"], "beneficiary_id": beneficiary["id"],
        "decision": payload.decision, "reason": reason, "at": now,
    })
    return {"ok": True, "status": new_status}


@api_router.post("/identity/verify-face")
async def verify_face(
    document: UploadFile = File(...),
    selfie: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") not in ("resident", "preceptor"):
        raise HTTPException(status_code=403, detail="Verificação facial exclusiva para residentes e preceptores")
    if current_user.get("identity_status") == "verified":
        raise HTTPException(status_code=409, detail="Identidade Verificada")
    if current_user.get("identity_status") == "manual_review_required":
        raise HTTPException(status_code=409, detail="Verificação aguardando análise da COREME")

    for uploaded in (document, selfie):
        if uploaded.content_type not in ("image/jpeg", "image/png"):
            raise HTTPException(status_code=400, detail="Envie JPG ou PNG")

    document_bytes = await document.read(FACE_MAX_BYTES + 1)
    selfie_bytes = await selfie.read(FACE_MAX_BYTES + 1)
    if not document_bytes or not selfie_bytes:
        raise HTTPException(status_code=400, detail="Imagens vazias")
    if len(document_bytes) > FACE_MAX_BYTES or len(selfie_bytes) > FACE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Cada imagem deve ter até 5 MB")

    try:
        comparison = await run_in_threadpool(_verify_face_images, document_bytes, selfie_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        logger.exception("Falha na comparação facial (OpenCV)")
        raise HTTPException(status_code=422, detail="Não foi possível comparar os rostos. Confira as imagens")

    # Uma submissão por vez: grava apenas o resultado técnico, não as imagens.
    updated = await db.users.update_one(
        {"id": current_user["id"],
         "identity_status": {"$nin": ["verified", "manual_review_required"]}},
        {"$set": {
            "identity_status": "manual_review_required",
            "identity_comparison": {
                "match": comparison["match"],
                "similarity": comparison.get("similarity"),
                "distance": comparison.get("distance"),
                "threshold": comparison.get("threshold"),
                "submitted_at": datetime.utcnow(),
            },
        }},
    )
    if updated.modified_count != 1:
        raise HTTPException(status_code=409, detail="Verificação já registrada ou aprovada")
    return {"user_id": current_user["id"], **comparison}


app.include_router(api_router)

@app.on_event("startup")
async def startup_db_client():
    try:
        admin = await db.users.find_one({"email": "admin@uepa.br"})

        bootstrap_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
        if not admin and bootstrap_password:
            admin_user = {
                "id": str(uuid.uuid4()),
                "email": "admin@uepa.br",
                "full_name": "Administrador COREME",
                "program": "Administração",
                "year": "N/A",
                "password": hash_password(bootstrap_password),
                "role": "admin",
                "created_at": datetime.utcnow(),
                "is_active": True
            }
            await db.users.insert_one(admin_user)
            logger.info("Administrador inicial criado; altere a senha após o primeiro acesso")

        await db.users.create_index("email", unique=True)
        await db.users.create_index("id")
        await db.submissions.create_index([("user_id", 1), ("reference_month", 1)])
        await db.submissions.create_index("reference_month")
        await db.users.create_index("coreme")
        await db.audit_events.create_index([("at", -1)])
        await db.coreme_confirmations.create_index([("coreme_id", 1), ("reference_month", 1)], unique=True)
        await db.coreme_reviews.create_index([("coreme_id", 1), ("beneficiary_id", 1), ("reference_month", 1)], unique=True)
        await db.commission_batches.create_index([("coreme_id", 1), ("reference_month", 1)], unique=True)
        await db.commission_reviews.create_index([("coreme_id", 1), ("beneficiary_id", 1), ("reference_month", 1)], unique=True)
        await db.payment_lists.create_index("reference_month", unique=True)
        await db.occurrences.create_index([("reference_month", 1), ("beneficiary_id", 1)])
        await db.benefit_declarations.create_index([("reference_month", 1), ("beneficiary_id", 1)], unique=True)

        print("Banco conectado com sucesso")

    except Exception as e:
        logger.exception("Falha na inicializacao do MongoDB; confira MONGO_URL e usuario Database Access")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
