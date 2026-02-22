from fastapi import FastAPI, APIRouter, HTTPException, Depends, UploadFile, File, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse, StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timedelta
import jwt
import bcrypt
import io
import zipfile
import base64
import aiofiles
from openpyxl import Workbook

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'coreme_uepa')]

# JWT Configuration
JWT_SECRET = os.environ.get('JWT_SECRET', 'uepa_coreme_secret_key_2025')
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# Upload directory
UPLOAD_DIR = ROOT_DIR / 'uploads' / 'pdfs'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Create the main app
app = FastAPI(title="COREME UEPA - Sistema de Envio de PDF")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Security
security = HTTPBearer()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== MODELS ====================

class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    program: str  # Programa de residência
    year: str  # R1, R2, R3, etc.

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class User(UserBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str = "resident"  # resident or admin
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    program: str
    year: str
    role: str
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
    reference_month: str  # Format: "YYYY-MM" (e.g., "2025-06")
    reference_month_name: str  # e.g., "Junho 2025"
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

# ==================== HELPERS ====================

MONTH_NAMES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
}

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

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
    """
    Calculates the reference month based on the "subsequent month rule":
    - Submissions from day 1-4 of current month refer to the PREVIOUS month
    """
    now = datetime.utcnow()
    current_day = now.day
    
    # Reference month is always the previous month
    if now.month == 1:
        ref_year = now.year - 1
        ref_month = 12
    else:
        ref_year = now.year
        ref_month = now.month - 1
    
    reference_month = f"{ref_year}-{ref_month:02d}"
    reference_month_name = f"{MONTH_NAMES_PT[ref_month]} {ref_year}"
    
    # Deadline calculation: day 4 at 23:59
    deadline_day = 4
    is_within_deadline = current_day <= deadline_day
    
    if is_within_deadline:
        deadline = datetime(now.year, now.month, deadline_day, 23, 59, 59)
    else:
        deadline = None  # Past deadline
    
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
    """Remove special characters and spaces from filename"""
    import re
    # Remove accents and special characters
    import unicodedata
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('ASCII')
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[\s]+', '', name)
    return name

# ==================== AUTH ROUTES ====================

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    # Check if email already exists
    existing = await db.users.find_one({"email": user_data.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    
    # Create user
    user_dict = {
        "id": str(uuid.uuid4()),
        "email": user_data.email.lower(),
        "full_name": user_data.full_name,
        "program": user_data.program,
        "year": user_data.year,
        "password": hash_password(user_data.password),
        "role": "resident",
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
            year=user_dict["year"],
            role=user_dict["role"],
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
            program=user.get("program", ""),
            year=user.get("year", ""),
            role=user["role"],
            created_at=user["created_at"],
            is_active=user.get("is_active", True)
        )
    )

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        full_name=current_user["full_name"],
        program=current_user.get("program", ""),
        year=current_user.get("year", ""),
        role=current_user["role"],
        created_at=current_user["created_at"],
        is_active=current_user.get("is_active", True)
    )

# ==================== SUBMISSION ROUTES ====================

@api_router.get("/submissions/deadline-info")
async def get_deadline_info():
    """Get current reference month and deadline information"""
    return get_reference_month_info()

@api_router.post("/submissions/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    # Check deadline
    deadline_info = get_reference_month_info()
    if not deadline_info["is_within_deadline"]:
        raise HTTPException(status_code=400, detail="Prazo de envio encerrado. O envio é permitido apenas do dia 1 ao dia 4 de cada mês.")
    
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Apenas arquivos PDF são aceitos")
    
    # Read file content
    content = await file.read()
    
    # Validate file size (10MB max)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo muito grande. Máximo permitido: 10MB")
    
    reference_month = deadline_info["reference_month"]
    
    # Check if user already submitted for this reference month
    existing = await db.submissions.find_one({
        "user_id": current_user["id"],
        "reference_month": reference_month
    })
    if existing:
        raise HTTPException(status_code=400, detail=f"Você já enviou um documento para {deadline_info['reference_month_name']}. Apenas 1 envio por mês é permitido.")
    
    # Generate unique filename
    sanitized_name = sanitize_filename(current_user["full_name"])
    month_name_sanitized = sanitize_filename(deadline_info["reference_month_name"])
    filename = f"{sanitized_name}_{month_name_sanitized}.pdf"
    file_path = UPLOAD_DIR / filename
    
    # Ensure unique filename
    counter = 1
    while file_path.exists():
        filename = f"{sanitized_name}_{month_name_sanitized}_{counter}.pdf"
        file_path = UPLOAD_DIR / filename
        counter += 1
    
    # Save file
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    # Create submission record
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
    
    # Log the submission
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
    """Get current user's submission history"""
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
    """Check if user already submitted for a specific month"""
    existing = await db.submissions.find_one({
        "user_id": current_user["id"],
        "reference_month": reference_month
    })
    return {"submitted": existing is not None}

# ==================== ADMIN ROUTES ====================

@api_router.get("/admin/submissions", response_model=List[SubmissionResponse])
async def get_all_submissions(
    reference_month: Optional[str] = None,
    program: Optional[str] = None,
    year: Optional[str] = None,
    admin_user: dict = Depends(get_admin_user)
):
    """Get all submissions with optional filters (admin only)"""
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
    """Get all users (admin only)"""
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
    """Get list of all programs"""
    programs = await db.users.distinct("program")
    return {"programs": [p for p in programs if p]}

@api_router.get("/admin/reference-months")
async def get_reference_months(admin_user: dict = Depends(get_admin_user)):
    """Get list of all reference months with submissions"""
    months = await db.submissions.distinct("reference_month")
    months_sorted = sorted(months, reverse=True)
    
    # Get month names
    result = []
    for m in months_sorted:
        year, month_num = m.split("-")
        month_name = f"{MONTH_NAMES_PT[int(month_num)]} {year}"
        result.append({"value": m, "label": month_name})
    
    return {"months": result}

@api_router.get("/admin/download/{submission_id}")
async def download_submission(submission_id: str, admin_user: dict = Depends(get_admin_user)):
    """Download a specific submission PDF"""
    submission = await db.submissions.find_one({"id": submission_id})
    if not submission:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    
    file_path = Path(submission["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no servidor")
    
    return FileResponse(
        path=str(file_path),
        filename=submission["file_name"],
        media_type="application/pdf"
    )

@api_router.get("/admin/export-excel")
async def export_excel(
    reference_month: Optional[str] = None,
    admin_user: dict = Depends(get_admin_user)
):
    """Export submissions to Excel"""
    query = {}
    if reference_month:
        query["reference_month"] = reference_month
    
    submissions = await db.submissions.find(query).sort("user_name", 1).to_list(10000)
    
    # Create workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Envios"
    
    # Headers
    headers = ["Nome", "Email", "Programa", "Ano", "Mês de Referência", "Status", "Data e Hora do Envio"]
    ws.append(headers)
    
    # Data
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
    
    # Save to buffer
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
    """Export all PDFs for a month as ZIP"""
    submissions = await db.submissions.find(
        {"reference_month": reference_month}
    ).sort("user_name", 1).to_list(10000)
    
    if not submissions:
        raise HTTPException(status_code=404, detail="Nenhum envio encontrado para este mês")
    
    # Create ZIP in memory
    buffer = io.BytesIO()
    
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for s in submissions:
            file_path = Path(s["file_path"])
            if file_path.exists():
                # Rename file to standard format
                sanitized_name = sanitize_filename(s["user_name"])
                month_name_sanitized = sanitize_filename(s["reference_month_name"])
                new_filename = f"{sanitized_name}_{month_name_sanitized}.pdf"
                
                # Read and add to zip
                zf.write(file_path, new_filename)
    
    buffer.seek(0)
    
    # Get month name for filename
    year, month_num = reference_month.split("-")
    month_name = f"{MONTH_NAMES_PT[int(month_num)]}_{year}"
    
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=PDFs_{month_name}.zip"}
    )

# ==================== SYSTEM CONFIG ROUTES ====================

@api_router.get("/config")
async def get_system_config():
    """Get system configuration (public)"""
    config = await db.system_config.find_one({"id": "system_config"})
    if not config:
        # Return default config
        return SystemConfig().dict()
    return config

@api_router.put("/admin/config")
async def update_system_config(
    config_update: SystemConfigUpdate,
    admin_user: dict = Depends(get_admin_user)
):
    """Update system configuration (admin only)"""
    update_data = {k: v for k, v in config_update.dict().items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    
    result = await db.system_config.update_one(
        {"id": "system_config"},
        {"$set": update_data},
        upsert=True
    )
    
    config = await db.system_config.find_one({"id": "system_config"})
    return config

# ==================== STATISTICS ====================

@api_router.get("/admin/stats")
async def get_stats(admin_user: dict = Depends(get_admin_user)):
    """Get dashboard statistics"""
    total_users = await db.users.count_documents({"role": "resident"})
    total_submissions = await db.submissions.count_documents({})
    
    # Current month submissions
    deadline_info = get_reference_month_info()
    current_month_submissions = await db.submissions.count_documents({
        "reference_month": deadline_info["reference_month"]
    })
    
    # Get submission count by program
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

# ==================== HEALTH CHECK ====================

@api_router.get("/")
async def root():
    return {"message": "COREME UEPA API", "status": "online"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== STARTUP ====================

@app.on_event("startup")
async def startup_db_client():
    # Create default admin user if not exists
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
    
    # Create indexes
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id")
    await db.submissions.create_index([("user_id", 1), ("reference_month", 1)])
    await db.submissions.create_index("reference_month")
    
    logger.info("COREME UEPA API started successfully")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
