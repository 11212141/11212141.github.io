import os
import sys

_PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
_VENV314_DIR = os.path.join(_PROJECT_DIR, ".venv314")
_VENV314_PYTHON = os.path.join(_PROJECT_DIR, ".venv314", "bin", "python")

if (
    os.environ.get("CARBON_COMMU_SKIP_VENV") != "1"
    and os.path.exists(_VENV314_PYTHON)
    and os.path.abspath(sys.prefix) != os.path.abspath(_VENV314_DIR)
):
    os.execv(_VENV314_PYTHON, [_VENV314_PYTHON, *sys.argv])

from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, Response, flash, current_app, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import text, func
from sqlalchemy import inspect as sa_inspect
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, timedelta, date as date_cls
from urllib.parse import urlencode
from PIL import Image, ImageDraw, ImageFont
from markupsafe import escape, Markup
from typing import Optional, Tuple, List, Set
from app.reading_tasks import (
    META_MODE,
    META_PARENT,
    META_REQUIREMENT,
    META_SDG_CODE,
    META_SOURCE,
    MODE_ASSIGNED,
    MODE_AUTONOMOUS,
    build_description,
    is_assigned_task,
    is_autonomous_task,
    source_label,
    task_body,
    task_meta,
)
import os, uuid, re, csv, io, base64, json, math

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(INSTANCE_DIR, "users.db")
os.makedirs(INSTANCE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
STATIC_VERSION = str(int(os.path.getmtime(os.path.join(BASE_DIR, "static", "app.css")))) if os.path.exists(os.path.join(BASE_DIR, "static", "app.css")) else "1"


if "kv_parse" not in globals():
    def kv_parse(text: str) -> dict:
        d = {}
        if not text:
            return d
        for m in re.finditer(r"^\[([A-Z_]+)\]=(.*)$", text, flags=re.M):
            d[m.group(1)] = m.group(2).strip()
        return d

if "kv_prefix" not in globals():
    def kv_prefix(text: str, kv: dict) -> str:
        head = "\n".join(f"[{k}]={v}" for k, v in kv.items())
        text = (text or "").strip()
        return (head + ("\n\n" + text if text else "")).strip()

if "kv_get_int" not in globals():
    def kv_get_int(d: dict, key: str, default: int = 0) -> int:
        try:
            return int(d.get(key, default))
        except Exception:
            return default

if "kv_get_str" not in globals():
    def kv_get_str(d: dict, key: str, default: str = "") -> str:
        v = d.get(key, default)
        return v if isinstance(v, str) else str(v)


app = Flask(__name__)
app.config.update(
    SECRET_KEY="yoursecretkey",
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,  
)
db = SQLAlchemy(app)
login_manager = LoginManager(app)

app.config["UPLOAD_FOLDER"] = UPLOAD_DIR

ALLOWED_EXTS = {"png", "jpg", "jpeg", "gif"}
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9]+$")


app.config.setdefault("LOCK_PAST_DATES", True)


SCHOOL_NAME = os.environ.get("SCHOOL_NAME", "國立臺東大學附設國小")
READING_SUBJECT = "閱讀"
CATEGORY_OPTIONS = [
    "國文", "國語", "英文", "英語", "數學", "生活", "自然", "社會",
    "健康", "體育", "健康與體育", "藝術", "音樂", "美勞", "綜合",
    "本土語", "閩南語", "客語", "資訊", READING_SUBJECT, "其他",
]
PREFERRED_SUBJECT_ORDER = [
    "國文", "國語", "英文", "英語", "數學", "生活", "自然", "社會",
    "健康", "體育", "健康與體育", "藝術", "音樂", "美勞", "綜合",
    "本土語", "閩南語", "客語", "資訊", READING_SUBJECT, "其他",
]
SUBJECT_ALIASES = {
    "國文": {"國文", "國語"},
    "國語": {"國文", "國語"},
    "英文": {"英文", "英語"},
    "英語": {"英文", "英語"},
    "健康": {"健康", "體育", "健康與體育"},
    "體育": {"健康", "體育", "健康與體育"},
    "健康與體育": {"健康", "體育", "健康與體育"},
    "藝術": {"藝術", "音樂", "美勞"},
    "音樂": {"藝術", "音樂", "美勞"},
    "美勞": {"藝術", "音樂", "美勞"},
    "本土語": {"本土語", "閩南語", "客語"},
    "閩南語": {"本土語", "閩南語", "客語"},
    "客語": {"本土語", "閩南語", "客語"},
}
MISSION_CATEGORY_OPTIONS = ["節能","節水","低碳交通","綠色飲食","資源回收","綠美化","其他"]  
SUSTAIN_DEFAULT_CATS = ["節能","節水","低碳交通","綠色飲食","資源回收","綠美化"]
GRADE_OPTIONS = [str(i) for i in range(1, 7)]
CLASS_OPTIONS = [str(i) for i in range(1, 21)]


ADMIN_SCHOOL_NAMES = {"國立臺東大學附設國小", "國立東大附設小學"}
def is_admin_school(name: str) -> bool:
    return (name or "").strip() in ADMIN_SCHOOL_NAMES


def _no_permission_redirect():
    """沒有權限時，帶訊息回上一頁；若沒有 referrer，就回 profile/home/根目錄。"""
    try:
        flash("沒有權限存取此頁面。", "warning")
    except Exception:
        pass
    target = request.args.get("next") or request.referrer
    if not target:
        if "profile" in app.view_functions:
            target = url_for("profile")
        elif "homework" in app.view_functions:
            target = url_for("homework")
        else:
            target = "/"
    return redirect(target)


def _normalize_path_text(value: Optional[str]) -> str:
    s = str(value or "").strip().replace("\\", "/")
    s = re.sub(r"/+", "/", s).lstrip("/")
    return s.replace("../", "").replace("..", "")


def _safe_upload_name(value: Optional[str]) -> str:
    return os.path.basename(_normalize_path_text(value))


from datetime import datetime, date

try:
    
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Taipei")

    def now_local() -> datetime:
        """系統統一的『現在時間』（台北時間、aware datetime）"""
        return datetime.now(TZ)

except Exception:
    
    TZ = None

    def now_local() -> datetime:
        """系統統一的『現在時間』（退回系統時區 datetime）"""
        return datetime.now()

def today_local() -> date:
    """系統統一的『今天』（搭配 now_local）"""
    return now_local().date()

from datetime import datetime 

def today_local() -> date_cls:
    return datetime.now().date()



def roles_required(*roles: str):
    """
    用法：@roles_required("teacher","leader")
    會先套用 @login_required，再檢查 current_user.role 是否在允許清單。
    """
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            user_role = getattr(current_user, "role", None)
            if roles and user_role not in roles:
                return _no_permission_redirect()
            return fn(*args, **kwargs)
        return wrapped
    return deco

def role_required(role: str):
    """單一角色別的語法糖：@role_required('teacher')"""
    return roles_required(role)



def parse_date(s: Optional[str]):
    """
    把 'YYYY-MM-DD' 或 'YYYY/MM/DD' 轉成 date；
    傳 None 或空字串會回 None。
    """
    if not s:
        return None
    s = s.strip().replace("/", "-")
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None

def is_locked_date(d: Optional[date_cls]) -> bool:
    """
    是否因設定而鎖定（日期已過）。
    這裡的「今天」已經是本地日期（透過 today() / today_local()），
    不再使用 UTC 直接換 date，避免日期差 1 天的問題。
    """
    return (
        current_app.config.get("LOCK_PAST_DATES", True)
        and d is not None
        and d < today()
    )


def _user_text_html(value: str | None) -> str:
    """
    將使用者輸入的純文字安全顯示為 HTML。
    會把貼上內容中的 <br> 當成換行，但其餘 HTML 仍維持轉義，避免誤植標籤或產生 XSS。
    """
    s = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.IGNORECASE)
    return str(escape(s)).replace("\n", "<br>")


def _desc_clean_html(desc: str) -> str:
    """
    移除描述開頭的 KV 標頭（支援多行或同一行連續的 [KEY]=value），
    然後安全轉義並把換行轉成 <br>。
    """
    if not desc:
        return ""
    s = desc.strip()
    
    s2 = re.sub(r"^(?:\[[A-Z_]+\]=.*(?:\r?\n|$))+", "", s, flags=re.M).strip()
    
    if s2 == s:
        s2 = re.sub(r"^\s*(?:\[[A-Z_]+\]=[^\r\n\[]*\s*)+", "", s).strip()
    return _user_text_html(s2)

def row(t: "Task") -> str:
    deadline = t.end_date or "—"
    img_name = task_img_name(t)
    img = img_html(img_name, maxw=280) if img_name else ""
    tools = (
        f"<a class='btn btn-sm btn-warning me-2' href='/edit_task/{t.id}'>改</a>"
        f"<a class='btn btn-sm btn-danger' href='/delete_task/{t.id}'>刪</a>"
    )
    scope_badge = f"<span class='badge bg-dark ms-1'>{_scope_label(t)}</span>"
    reqs = _req_badges_from_desc(t.description or "")
    return (
        "<tr>"
        f"<td class='text-nowrap'>{t.start_date}</td>"
        "<td>"
        f"<div class='fw-bold'>{t.title} "
        f"<span class='badge bg-secondary ms-1'>永續閱讀</span>"
        f"{scope_badge}<span class='badge bg-info ms-1'>+{t.points or 0} 分</span>{reqs}</div>"
        f"<div class='small text-muted'>閱讀分類：{(t.mission_category or '其他')} · 截止：{deadline} · 建立者：{display_name_of(t.created_by)}</div>"
        f"<div class='mt-1'>{_desc_clean_html(t.description or '')}</div>"
        f"{img}"
        "</td>"
        f"<td class='text-end'>{tools}</td>"
        "</tr>"
    )


def _get_class_reading_goal(unit, grade, class_no):
    """
    回傳 dict:
      - per_sdg_target: 每一 SDG 類別本月目標分數（預設 10）
      - total_month_target: 本月總分目標（預設 0 = 不啟用）
    由 Task.task_type == 'goal' 最新一筆決定。
    """
    if not grade or not class_no:
        return {"per_sdg_target": 10, "total_month_target": 0}

    try:
        t = (
            Task.query.filter_by(  
                unit=unit,
                grade=grade,
                class_no=class_no,
                task_type="goal",
            )
            .order_by(Task.id.desc())
            .first()
        )
    except Exception:
        t = None

    if not t:
        return {"per_sdg_target": 10, "total_month_target": 0}

    payload = {}
    try:
        payload = _json_loads(t.description)
    except Exception:
        payload = {}

    return {
        "per_sdg_target": _safe_int(payload.get("per_sdg_target"), 10),
        "total_month_target": _safe_int(payload.get("total_month_target"), 0),
    }




def _ensure_reading_area_task_for_student(user):
    unit = getattr(user, "unit", None)
    grade = getattr(user, "grade", None)
    class_no = getattr(user, "class_no", None)

    try:
        q = Task.query.filter_by(  
            unit=unit,
            grade=grade,
            class_no=class_no,
            task_type="mission",
            mission_category="閱讀專區",
        )
        t = q.order_by(Task.id.desc()).first()
    except Exception:
        t = None

    if t:
        return t

    
    t = Task(
        title="永續閱讀專區",
        description="本區為自主閱讀認證區，學生可自由選書閱讀並完成閱讀心得。",
        created_by=getattr(user, "username", "system"),
        category=None,
        mission_category="閱讀專區",
        points=5,
        start_date=_today(),
        end_date=None,
        unit=unit,
        grade=grade,
        class_no=class_no,
        is_school_wide=1,
        task_type="mission",
        is_view_only=0,
    )
    db.session.add(t)
    db.session.commit()
    return t

def _go_back(msg: Optional[str] = None, level: str = "success", fallback_ep: str = "teacher"):
    """帶提示回上一頁；沒有 referrer 就回老師頁。"""
    if msg:
        try:
            flash(msg, level)
        except Exception:
            pass
    target = request.args.get("next") or request.referrer
    try:
        return redirect(target) if target else redirect(url_for(fallback_ep))
    except Exception:
        return redirect("/teacher")

def _teacher_handles_class(user: "User", grade: Optional[str], class_no: Optional[str]) -> bool:
    if not user or not grade or not class_no:
        return False
    return is_homeroom_of(user, grade, class_no) or ((str(grade), str(class_no)) in _teacher_scopes(user))

def _can_review_completion(user: "User", c: "CompletedTask") -> bool:
    """誰能審：leader/admin 全部；teacher 限導師班或任教班。"""
    if not user or not c or not c.task:
        return False
    t = c.task
    if t.unit != SCHOOL_NAME:
        return False
    if getattr(user, "role", None) in ("leader", "admin"):
        return True
    if getattr(user, "role", None) != "teacher":
        return False
    
    g, cl = (t.grade, t.class_no)
    if (not g or not cl) and c.student_name:
        stu = User.query.filter_by(username=c.student_name).first()
        if stu:
            g, cl = stu.grade, stu.class_no
    return _teacher_handles_class(user, g, cl)

@app.route("/approve/<int:cid>")
@login_required
def approve(cid):
    c = db.session.get(CompletedTask, cid)
    if not c:
        return _go_back("找不到這筆提交。", "warning")
    if not _can_review_completion(current_user, c):
        return _go_back("沒有審核權限。", "warning")
    c.approved = 1
    c.reject_reason = None
    c.approved_by = getattr(current_user, "username", None)
    c.approved_at = datetime.now()
    db.session.commit()
    return _go_back("已核准。", "success")

@app.route("/reject/<int:cid>", methods=["GET", "POST"])
@login_required
def reject(cid):
    c = db.session.get(CompletedTask, cid)
    if not c:
        return _go_back("找不到這筆提交。", "warning")
    if not _can_review_completion(current_user, c):
        return _go_back("沒有審核權限。", "warning")

    if request.method == "POST":
        reason = (request.form.get("reason") or "").strip()
        if not reason:
            flash("請填寫駁回理由。", "warning")
        else:
            c.approved = 0
            c.reject_reason = reason
            c.approved_by = getattr(current_user, "username", None)
            c.approved_at = datetime.now()
            db.session.commit()
            return _go_back("已駁回並附上理由。", "success")

    
    t = c.task
    title = t.title if t else f"提交#{cid}"
    form_html = (
        "<div class='card border-0 shadow-sm mx-auto' style='max-width:720px;'>"
        "<div class='card-body'>"
        f"<h5 class='card-title mb-3'>駁回：{escape(title)}</h5>"
        "<form method='post'>"
        "<div class='mb-3'>"
        "<label class='form-label'>駁回理由（必填）</label>"
        "<textarea name='reason' class='form-control' rows='3' placeholder='請輸入駁回理由' required></textarea>"
        "</div>"
        "<div class='d-flex gap-2'>"
        "<button class='btn btn-outline-danger'>送出駁回</button>"
        "<a class='btn btn-secondary' href='javascript:history.back()'>取消</a>"
        "</div>"
        "</form>"
        "</div></div>"
    )
    return page("駁回提交", form_html)


def _date_in_range(d: Optional[date_cls], s: Optional[date_cls], e: Optional[date_cls]) -> bool:
    return (d is not None and s is not None and e is not None and s <= d <= e)

def _weekday_0_is_mon(d: date_cls) -> int:
    
    return int(d.weekday())

def current_semester(unit: str, d: Optional[date_cls] = None) -> "Semester | None":
    d = (d or today())
    return (Semester.query
            .filter_by(unit=unit)
            .filter(Semester.start_date <= d, Semester.end_date >= d)
            .order_by(Semester.start_date.desc())
            .first())

def _week_spans(sem: "Semester") -> List[Tuple[int, date_cls, date_cls]]:
    """依學期起迄產生週區間（週一~週日）。"""
    spans: List[Tuple[int, date_cls, date_cls]] = []
    cur = sem.start_date - timedelta(days=sem.start_date.weekday())  
    idx = 1
    while cur <= sem.end_date:
        s = cur
        e = min(cur + timedelta(days=6), sem.end_date)
        spans.append((idx, s, e))
        cur = e + timedelta(days=1)
        idx += 1
    return spans

def semester_of_date(unit: str, d: Optional[date_cls]) -> Tuple[Optional["Semester"], Optional[int]]:
    """回傳 (學期物件, 第幾週)；週次優先查 semester_week，沒有就動態推算。"""
    d = d or today()
    sem = current_semester(unit, d)
    if not sem:
        return None, None
    wk = (SemesterWeek.query
          .filter_by(semester_id=sem.id)
          .filter(SemesterWeek.start_date <= d, SemesterWeek.end_date >= d)
          .first())
    if wk:
        return sem, int(wk.week_no)
    start_monday = sem.start_date - timedelta(days=sem.start_date.weekday())
    delta = (d - start_monday).days
    week_no = delta // 7 + 1
    return sem, max(1, week_no)


def semester_weeks_view(sid):
    if getattr(current_user, "role", None) not in ("leader", "admin"):
        return redirect(url_for("profile") if "profile" in app.view_functions else "/")

    sem = db.session.get(Semester, sid)
    if not sem or sem.unit != SCHOOL_NAME:
        flash("學期不存在", "warning")
        return redirect(url_for("semester_manage"))

    def _overlap(ws, we, others):
        for o in others:
            if not (we < o.start_date or o.end_date < ws):  
                return True
        return False

    
    if request.method == "POST":
        try:
            no = int((request.form.get("week_no") or "0"))
        except Exception:
            no = 0
        ws = parse_date(request.form.get("start_date"))
        we = parse_date(request.form.get("end_date"))
        if not (no and ws and we and ws <= we):
            flash("請填正確的週次與日期", "warning")
            return redirect(url_for("semester_weeks", sid=sid))
        if not (sem.start_date <= ws <= sem.end_date and sem.start_date <= we <= sem.end_date):
            flash("週次日期必須落在學期範圍內", "warning")
            return redirect(url_for("semester_weeks", sid=sid))

        
        others = (SemesterWeek.query
                  .filter_by(semester_id=sid)
                  .filter(SemesterWeek.week_no != no)
                  .all())
        if _overlap(ws, we, others):
            flash("週次日期區間不可與其他週次重疊", "warning")
            return redirect(url_for("semester_weeks", sid=sid))

        rec = SemesterWeek.query.filter_by(semester_id=sid, week_no=no).first()
        if rec:
            rec.start_date, rec.end_date = ws, we
        else:
            db.session.add(SemesterWeek(semester_id=sid, week_no=no, start_date=ws, end_date=we))
        db.session.commit()
        flash("已儲存週次", "success")
        return redirect(url_for("semester_weeks", sid=sid))

    
    wks = (SemesterWeek.query.filter_by(semester_id=sid)
           .order_by(SemesterWeek.week_no.asc()).all())
    rows = "".join(
        (
            "<tr>"
            f"<td>{w.week_no}</td>"
            f"<td>{w.start_date} ~ {w.end_date}</td>"
            f"<td><a class='btn btn-sm btn-outline-danger' href='{url_for('semester_week_delete', sid=sid, wid=w.id)}' "
            "onclick=\"return confirm('確定刪除此週次？');\">刪除</a></td>"
            "</tr>"
        )
        for w in wks
    )

    
    cal_imgs = (SemesterCalendarImage.query
                .filter_by(semester_id=sid)
                .order_by(SemesterCalendarImage.created_at.desc())
                .all())
    cal_grid = "".join(
        (
            "<div class='col-md-4 col-lg-3'>"
            "<div class='card p-2 h-100'>"
            f"<div class='small text-muted mb-1'>{im.created_at.strftime('%Y-%m-%d')}</div>"
            f"{img_html(im.file, maxw=320)}"
            "</div></div>"
        )
        for im in cal_imgs
    ) or "<div class='text-muted'>尚無行事曆圖片</div>"

    
    if "shortcut_buttons_html" in globals():
        sbh = shortcut_buttons_html()
    else:
        def shortcut_buttons_html():  
            return ""
        sbh = ""

    html = (
        f"<div class='alert alert-info'>學期：<b>{escape(sem.name)}</b>（{sem.start_date} ~ {sem.end_date}）</div>"
        "<h6 class='mb-2'>新增/覆蓋週次</h6>"
        "<form method='post' class='row g-2 mb-4'>"
        "<div class='col-2'><input type='number' min='1' class='form-control' name='week_no' placeholder='週次' required></div>"
        "<div class='col-3'><input type='date' class='form-control' name='start_date' required></div>"
        "<div class='col-3'><input type='date' class='form-control' name='end_date' required></div>"
        "<div class='col-2'><button class='btn btn-success w-100'>儲存</button></div>"
        "</form>"

        "<div class='table-responsive mb-4'><table class='table table-sm'>"
        "<thead><tr><th>週</th><th>起訖</th><th>操作</th></tr></thead><tbody>"
        + rows + "</tbody></table></div>"

        "<div class='d-flex align-items-center justify-content-between mb-2'>"
        "<h6 class='m-0'>本學期行事曆</h6>"
        f"<a class='btn btn-sm btn-outline-primary' href='{url_for('semester_calendar', sid=sid)}'>上傳 / 管理行事曆圖片</a>"
        "</div>"
        f"<div class='row g-3'>{cal_grid}</div>"

        f"<div class='mt-3'><a class='btn btn-outline-secondary' href='{url_for('semester_manage')}'>返回學期列表</a></div>"
    )
    return page("週次管理", sbh + html)


if 'semester_weeks' in app.view_functions:
    app.view_functions['semester_weeks'] = semester_weeks_view
else:
    app.add_url_rule(
        "/semester/<int:sid>/weeks",
        endpoint="semester_weeks",
        view_func=semester_weeks_view,
        methods=["GET", "POST"],
    )


def _set_task_image_if_possible(task_obj=None, filename=None):
    """
    安全地把圖片檔名寫進 Task 物件中第一個可用的圖片欄位。
    支援欄位：image, attachment_image, image_name, image_file, cover_image,
             photo, picture, img, mission_image, homework_image, images
    """
    if task_obj is None or not filename:
        return
    fields = (
        "image", "attachment_image", "image_name", "image_file",
        "cover_image", "photo", "picture", "img",
        "mission_image", "homework_image", "images"
    )
    for f in fields:
        if hasattr(task_obj, f):
            if f == "images":
                cur = getattr(task_obj, f)
                try:
                    if isinstance(cur, list):
                        cur.insert(0, filename)
                        setattr(task_obj, f, cur)
                    elif isinstance(cur, str) and cur.strip():
                        setattr(task_obj, f, f"{filename},{cur}")
                    else:
                        setattr(task_obj, f, filename)
                except Exception:
                    setattr(task_obj, f, filename)
            else:
                setattr(task_obj, f, filename)
            break

def _collect_upload_photos():
    """
    Collect information about uploaded photos for management UIs.
    """
    root = app.config.get("UPLOAD_FOLDER") or UPLOAD_DIR
    photos = []
    if os.path.isdir(root):
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                url = f"/uploads/{rel}"
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    mtime = ""
                photos.append({"rel": rel, "url": url, "mtime": mtime})
    photos.sort(key=lambda x: x["rel"])
    return photos

def is_sustain_task(t) -> bool:
    """
    判斷任務是否屬於永續任務或特別分類（供多處重複使用）。
    """
    if getattr(t, "task_type", "homework") == "mission":
        return True
    sustain_opts = globals().get("SUSTAIN_OPTIONS")
    if isinstance(sustain_opts, (list, tuple, set)):
        cats = list(sustain_opts)
    else:
        cats = SUSTAIN_DEFAULT_CATS
    cats = [c for c in cats if str(c) != "其他"]
    category = (getattr(t, "category", None) or "其他")
    mission_cat = getattr(t, "mission_category", None) or ""
    return (category in cats) or (mission_cat in cats)

def task_img_name(t):
    """從 Task 取出一張要顯示的圖片檔名（盡可能相容各種欄位命名）。"""
    for key in ("image","attachment_image","image_name","image_file",
                "cover_image","photo","picture","img",
                "mission_image","homework_image"):
        if hasattr(t, key):
            val = getattr(t, key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if hasattr(t, "images"):
        val = getattr(t, "images")
        if isinstance(val, list) and val:
            fst = val[0]
            if isinstance(fst, str) and fst.strip():
                return fst.strip()
        if isinstance(val, str) and val.strip():
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if parts:
                return parts[0]
    if hasattr(t, "extra"):
        val = getattr(t, "extra")
        try:
            data = json.loads(val) if isinstance(val, str) else (val or {})
            for key in ["image", "cover_image", "picture", "img"]:
                if key in (data or {}):
                    cand = data[key]
                    if isinstance(cand, str) and cand.strip():
                        return cand.strip()
                    if isinstance(cand, list) and cand:
                        return str(cand[0]).strip()
        except Exception:
            pass
    return None

def _img_src(name: str) -> str:
    if not name:
        return ""
    s = (name or "").strip().replace("\\", "/")
    if s.startswith("http://") or s.startswith("https://") or s.startswith("/static/"):
        return s
    s = re.sub(r"^(?:/)?(?:uploads/)+", "", s)
    return f"/uploads/{s}"

def img_html(name: str, maxw: int = 220) -> str:
    if not name:
        return ""
    src = escape(_img_src(name))
    width = max(maxw or 0, 280)
    return (
        "<figure class='attachment-preview'>"
        f"<a target='_blank' href='{src}' class='attachment-preview__link' title='點擊放大圖片'>"
        f"<img src='{src}' class='attachment-preview__image' style='max-width:{width}px;' alt='附圖'>"
        "<span class='attachment-preview__hint'>點擊放大</span>"
        "</a>"
        "</figure>"
    )

def diary_prompt_img_name(prompt) -> str | None:
    for key in ("image", "attachment_image", "image_name", "photo", "picture", "img"):
        if hasattr(prompt, key):
            val = getattr(prompt, key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None

def _filter_homework_items(tasks):
    items = []
    for t in tasks or []:
        if (getattr(t, "task_type", "homework") == "homework" or getattr(t, "task_type", None) is None):
            if not is_sustain_task(t):
                items.append(t)
    return items

def render_homework_table(tasks, heading: str | None = "作業區", show_empty: bool = False) -> str:
    hw_items = _filter_homework_items(tasks)
    if not hw_items:
        return "<div class='text-muted small'>今日沒有老師布置的作業。</div>" if show_empty else ""

    preferred = PREFERRED_SUBJECT_ORDER
    base_order = [s for s in preferred if s in CATEGORY_OPTIONS] + [c for c in CATEGORY_OPTIONS if c not in preferred]
    by_cat = {}
    for t in hw_items:
        by_cat.setdefault(t.category or "其他", []).append(t)
    present_order = [c for c in base_order if c in by_cat] + sorted([c for c in by_cat.keys() if c not in base_order])

    def hw_cell(items):
        out = []
        for t in items:
            deadline = f"<small class='text-muted ms-2'>截止：{t.end_date}</small>" if t.end_date else ""
            thumb = img_html(task_img_name(t))
            title_html = f"<div class='fw-bold'>{escape(t.title)}</div>"
            out.append(
                "<div class='mb-3'>"
                f"{title_html}"
                f"<div class='mt-1'>{_desc_clean_html(t.description or '')}</div>"
                f"{thumb}"
                f"<div class='small text-muted mt-1'>教師：{escape(display_name_of(t.created_by) or '')}{deadline}</div>"
                "</div>"
            )
        return "".join(out)

    rows = "".join(
        f"<tr><th class='homework-subject-cell text-nowrap'>{cat}</th><td>{hw_cell(by_cat.get(cat, []))}</td></tr>"
        for cat in present_order
    )
    heading_html = f"<h4 class='mt-4'> {heading}</h4>" if heading else ""
    return (
        f"{heading_html}"
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th style='width:8rem'>科目</th><th>內容</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


from datetime import datetime, date, timedelta

def _now() -> datetime:
    return datetime.now()

def _today() -> date:
    """優先用你專案裡原本的 today()，沒有就用系統今天。"""
    try:
        return today()  
    except Exception:
        return date.today()

def _week_range(d: date):
    """回傳這一天所在週的 (週一, 週日)。"""
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end

def _month_range(d: date):
    """回傳這一天所在月份的 (當月第一天, 當月最後一天)。"""
    first = d.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1, day=1)
    else:
        nxt = first.replace(month=first.month + 1, day=1)
    last = nxt - timedelta(days=1)
    return first, last

def _semester_range(d: date):
    """
    回傳 (學期開始日, 學期結束日)。

    先用簡單二學期制：
      - 第 1 學期：當年 8/1 ~ 次年 1/31
      - 第 2 學期：當年 2/1 ~ 7/31
      - 1 月視為上一學年度第 1 學期尾聲
    之後如果你有自己的學期表，只要改這裡即可。
    """
    year = d.year
    if d.month >= 8:
        
        start = date(year, 8, 1)
        end = date(year + 1, 1, 31)
    elif d.month >= 2:
        
        start = date(year, 2, 1)
        end = date(year, 7, 31)
    else:
        
        start = date(year - 1, 8, 1)
        end = date(year, 1, 31)
    return start, end


def semester_range(d: date) -> tuple[date, date]:
    return _semester_range(d)

def get_term_range(d: date | None = None) -> tuple[date, date]:
    """外部如果還在呼叫 get_term_range，就走新的 _semester_range。"""
    return _semester_range(d or _today())

def _is_staff():
    return getattr(current_user, "role", None) in ("teacher","leader","admin")

def _is_homeroom_teacher():
    return (getattr(current_user, "role", None) == "teacher"
            and str(getattr(current_user, "is_homeroom", 0)) == "1"
            and getattr(current_user, "grade", None) and getattr(current_user, "class_no", None))

def _can_delete_leave(lr: "LeaveRequest") -> bool:
    
    if getattr(current_user, "role", None) in ("leader","admin"):
        return True
    if _is_homeroom_teacher():
        return (lr.grade == current_user.grade and lr.class_no == current_user.class_no)
    return False

def _scope_label(t) -> str:
    """顯示任務適用範圍字串。"""
    try:
        if getattr(t, "is_school_wide", 0) == 1:
            return "全校"
        g = getattr(t, "grade", None)
        c = getattr(t, "class_no", None)
        if g and c:
            return f"{g}年{c}班"
        if g and not c:
            return f"{g}年"
        return "未設定"
    except Exception:
        return "未設定"


def can_view_contact(viewer, target: "User") -> bool:
    return True


def display_name_of(username: Optional[str]) -> str:
    if not username:
        return ""
    u = User.query.filter_by(username=username).first()
    return (u.display_name or u.username) if u else (username or "")

def is_homeroom_of(user: "User", grade: str, class_no: str) -> bool:
    return (user and getattr(user, "role", None) == "teacher" and str(getattr(user, "is_homeroom", 0)) == "1"
            and str(user.grade) == str(grade) and str(user.class_no) == str(class_no))

def _teacher_scopes(user: "User") -> Set[Tuple[str, str]]:
    """此老師授課的 (年級, 班級) 集合。"""
    if not user or getattr(user, "role", None) not in ("teacher","leader","admin"):
        return set()
    rows = TeachingAssignment.query.filter_by(teacher_username=user.username).all()
    return {(str(r.grade), str(r.class_no)) for r in rows}


def ensure_diary_submission_feedback_columns():
    insp = sa_inspect(db.engine)
    try:
        cols = {c["name"] for c in insp.get_columns("diary_submission")}
    except Exception:
        cols = set()
    alters = []
    if "teacher_comment" not in cols:
        alters.append("ALTER TABLE diary_submission ADD COLUMN teacher_comment TEXT")
    if "teacher_commented_by" not in cols:
        alters.append("ALTER TABLE diary_submission ADD COLUMN teacher_commented_by VARCHAR(64)")
    if "teacher_commented_at" not in cols:
        alters.append("ALTER TABLE diary_submission ADD COLUMN teacher_commented_at DATETIME")
    if alters:
        try:
            with db.engine.begin() as conn:
                for sql in alters:
                    conn.execute(text(sql))
        except Exception:
            pass


def ensure_user_profile_columns():
    insp = sa_inspect(db.engine)
    
    try:
        cols = {c["name"] for c in insp.get_columns("user")}
        table_name = "user"
    except Exception:
        try:
            cols = {c["name"] for c in insp.get_columns("users")}
            table_name = "users"
        except Exception:
            cols, table_name = set(), "user"

    missing_sql = []
    def add(col, ddl):
        if col not in cols:
            missing_sql.append(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")

    add("email",         "email VARCHAR(150)")
    add("phone",         "phone VARCHAR(50)")
    add("line_id",       "line_id VARCHAR(100)")
    add("title",         "title VARCHAR(100)")
    add("subjects",      "subjects TEXT")
    add("bio",           "bio TEXT")
    add("office_hours",  "office_hours VARCHAR(200)")
    add("contact_pref",  "contact_pref VARCHAR(20)")
    add("avatar",        "avatar VARCHAR(255)")

    if missing_sql:
        with db.engine.begin() as conn:
            for sql in missing_sql:
                try:
                    conn.execute(text(sql))
                except Exception:
                    pass


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username     = db.Column(db.String(150), unique=True, nullable=False)
    display_name = db.Column(db.String(150))
    password     = db.Column(db.String(150), nullable=False)
    role         = db.Column(db.String(50),  nullable=False)
    unit         = db.Column(db.String(150))
    grade        = db.Column(db.String(10))
    class_no     = db.Column(db.String(10))
    is_homeroom  = db.Column(db.Integer, default=0)  
    email        = db.Column(db.String(150))
    phone        = db.Column(db.String(50))
    line_id      = db.Column(db.String(100))
    title        = db.Column(db.String(100))
    subjects     = db.Column(db.Text)
    bio          = db.Column(db.Text)
    office_hours = db.Column(db.String(200))
    contact_pref = db.Column(db.String(20))
    avatar       = db.Column(db.String(255))

class TeachingAssignment(db.Model):
    """科任老師指派表：一位老師可有多筆「科目 × 年級 × 班級」"""
    __tablename__ = "teaching_assignment"
    id = db.Column(db.Integer, primary_key=True)
    teacher_username = db.Column(db.String(150), nullable=False, index=True)
    subject         = db.Column(db.String(100), nullable=False)   
    grade           = db.Column(db.String(10),  nullable=False)
    class_no        = db.Column(db.String(10),  nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.now)
    __table_args__ = (
        db.UniqueConstraint("teacher_username", "subject", "grade", "class_no",
                            name="uq_teacher_subject_class"),
    )


class SemesterCalendarImage(db.Model):
    __tablename__ = "semester_calendar_image"
    id = db.Column(db.Integer, primary_key=True)
    semester_id = db.Column(db.Integer, db.ForeignKey("semester.id"), nullable=False, index=True)
    unit = db.Column(db.String(150), nullable=False, index=True)
    file = db.Column(db.String(255), nullable=False)   
    note = db.Column(db.String(200))
    uploader = db.Column(db.String(150))
    created_at = db.Column(db.DateTime, default=datetime.now)
    semester = db.relationship("Semester", backref="calendar_images")


SEMESTER_DIR = os.path.join(UPLOAD_DIR, "semester")
os.makedirs(SEMESTER_DIR, exist_ok=True)

def create_teaching_assignment_if_missing():
    """
    SQLite 友善：若無 teaching_assignment 表則建立；若無索引則補。
    放在啟動流程即可呼叫，多次呼叫安全。
    """
    try:
        with db.engine.begin() as c:
            try:
                c.execute(text("SELECT 1 FROM teaching_assignment LIMIT 1"))
            except Exception:
                c.execute(text("""
                    CREATE TABLE IF NOT EXISTS teaching_assignment (
                        id INTEGER PRIMARY KEY,
                        teacher_username VARCHAR(150) NOT NULL,
                        subject VARCHAR(100) NOT NULL,
                        grade VARCHAR(10) NOT NULL,
                        class_no VARCHAR(10) NOT NULL,
                        created_at DATETIME,
                        CONSTRAINT uq_teacher_subject_class
                            UNIQUE (teacher_username, subject, grade, class_no)
                    )
                """))
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_ta_teacher ON teaching_assignment(teacher_username)"))
    except Exception:
        pass


class Semester(db.Model):
    __tablename__ = "semester"
    id = db.Column(db.Integer, primary_key=True)
    unit = db.Column(db.String(150), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)       
    start_date = db.Column(db.Date, nullable=False)
    end_date   = db.Column(db.Date, nullable=False)
    created_by = db.Column(db.String(150))
    created_at = db.Column(db.DateTime, default=datetime.now)

class SemesterWeek(db.Model):
    __tablename__ = "semester_week"
    id = db.Column(db.Integer, primary_key=True)
    semester_id = db.Column(db.Integer, db.ForeignKey("semester.id"), index=True, nullable=False)
    week_no     = db.Column(db.Integer, nullable=False)     
    start_date  = db.Column(db.Date, nullable=False)
    end_date    = db.Column(db.Date, nullable=False)
    semester    = db.relationship("Semester", backref="weeks")

class TimetableImage(db.Model):
    """
    課表圖片（只存檔名與歸屬資訊）
    kind: 'teacher' | 'class'
    - teacher：owner_username = 老師帳號；可上傳多張。
    - class  ：grade/class_no 指向班級；可上傳多張；uploader 記錄誰傳的（老師/組長）。
    """
    __tablename__ = "timetable_image"
    id = db.Column(db.Integer, primary_key=True)
    unit = db.Column(db.String(150), nullable=False, index=True)
    kind = db.Column(db.String(20),  nullable=False, index=True)         
    file = db.Column(db.String(255), nullable=False)                      
    owner_username = db.Column(db.String(150), index=True)                
    grade = db.Column(db.String(10), index=True)                          
    class_no = db.Column(db.String(10), index=True)                       
    note = db.Column(db.String(200))
    uploader = db.Column(db.String(150))                                  
    created_at = db.Column(db.DateTime, default=datetime.now)


TIMETABLE_DIR = os.path.join(UPLOAD_DIR, "timetable")
os.makedirs(TIMETABLE_DIR, exist_ok=True)


class ParentReminder(db.Model):
    """家長提醒設定：每天固定時間提醒（登入頁內提醒；簡訊/Email 另建背景工作）"""
    __tablename__ = "parent_reminder"
    id = db.Column(db.Integer, primary_key=True)
    parent_name = db.Column(db.String(150), nullable=False, unique=True, index=True)
    hour = db.Column(db.Integer, default=20)    
    minute = db.Column(db.Integer, default=0)   
    enabled = db.Column(db.Integer, default=0)  
    created_at = db.Column(db.DateTime, default=datetime.now)

class ParentSignature(db.Model):
    """家長電子簽名：一天一類（diary/homework）一份，可覆蓋。"""
    __tablename__ = "parent_signature"
    id = db.Column(db.Integer, primary_key=True)
    parent_name  = db.Column(db.String(150), nullable=False, index=True)
    student_name = db.Column(db.String(150), nullable=False, index=True)
    date         = db.Column(db.Date, nullable=False, index=True)
    scope        = db.Column(db.String(20), nullable=False)  
    image        = db.Column(db.String(255))                 
    note         = db.Column(db.String(200))                 
    created_at   = db.Column(db.DateTime, default=datetime.now)
    __table_args__ = (
        db.UniqueConstraint("parent_name", "student_name", "date", "scope",
                            name="uq_parent_sign_once_per_day"),
    )

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    created_by  = db.Column(db.String(150), nullable=False)
    category    = db.Column(db.String(100))                  
    mission_category = db.Column(db.String(100))             
    points      = db.Column(db.Integer)
    start_date  = db.Column(db.Date)
    end_date    = db.Column(db.Date)
    unit        = db.Column(db.String(150))
    grade       = db.Column(db.String(10))
    class_no    = db.Column(db.String(10))
    is_school_wide = db.Column(db.Integer, default=0)        
    is_view_only   = db.Column(db.Integer, default=0)        
    task_type      = db.Column(db.String(20), default="homework")  
    image          = db.Column(db.String(255))

class CompletedTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_name = db.Column(db.String(150), nullable=False)
    task_id      = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=False)
    proof_image  = db.Column(db.String(255))
    reflection   = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    approved     = db.Column(db.Integer)  
    approved_by  = db.Column(db.String(150))
    approved_at  = db.Column(db.DateTime)
    reject_reason= db.Column(db.Text)
    
    with_parent  = db.Column(db.Integer, default=0)
    has_reflection = db.Column(db.Integer, default=0)
    has_image    = db.Column(db.Integer, default=0)
    calc_points  = db.Column(db.Integer)
    streak3_bonus= db.Column(db.Integer, default=0)
    task = db.relationship("Task", backref="completions")

class ParentChild(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    parent_name  = db.Column(db.String(150), nullable=False)
    student_name = db.Column(db.String(150), nullable=False)


class DiaryPrompt(db.Model):
    """老師發布的日記題目（限定到本班）"""
    __tablename__ = "diary_prompt"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    instruction = db.Column(db.Text)
    unit = db.Column(db.String(150), nullable=False)
    grade = db.Column(db.String(10), nullable=False)
    class_no = db.Column(db.String(10), nullable=False)
    created_by = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    image = db.Column(db.String(255))

class DiarySubmission(db.Model):
    __tablename__ = "diary_submission"
    id = db.Column(db.Integer, primary_key=True)
    prompt_id = db.Column(db.Integer, db.ForeignKey('diary_prompt.id'), index=True, nullable=False)
    student_name = db.Column(db.String(64), index=True, nullable=False)
    content = db.Column(db.Text)
    share_to_parent = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    teacher_comment = db.Column(db.Text)
    teacher_commented_by = db.Column(db.String(64))
    teacher_commented_at = db.Column(db.DateTime)
    
    image = db.Column(db.String(512))

class CommNote(db.Model):
    """親師交流：三方皆可發（teacher / parent / student）"""
    __tablename__ = "comm_note"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text)
    image = db.Column(db.String(255))
    unit = db.Column(db.String(150), nullable=False)
    grade = db.Column(db.String(10), nullable=False)
    class_no = db.Column(db.String(10), nullable=False)
    created_by = db.Column(db.String(150), nullable=False, index=True)
    author_role = db.Column(db.String(20), nullable=False)          
    visible_to_student = db.Column(db.Integer, default=1)
    visible_to_parent  = db.Column(db.Integer, default=1)
    student_username = db.Column(db.String(150))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LeaveRequest(db.Model):
    __tablename__ = "leave_request"
    id = db.Column(db.Integer, primary_key=True)

    
    applicant_name  = db.Column(db.String(150), nullable=False)  
    applicant_role  = db.Column(db.String(20),  nullable=False)  

    
    student_name    = db.Column(db.String(150), nullable=False, index=True)
    student_display = db.Column(db.String(150))                  
    unit            = db.Column(db.String(150))
    grade           = db.Column(db.String(10))
    class_no        = db.Column(db.String(10))

    
    leave_type      = db.Column(db.String(30), nullable=False)   
    reason          = db.Column(db.Text)

    
    start_date      = db.Column(db.Date, nullable=False)
    end_date        = db.Column(db.Date,   nullable=False)

    
    status          = db.Column(db.Integer)
    reviewed_by     = db.Column(db.String(150))
    reviewed_at     = db.Column(db.DateTime)
    review_note     = db.Column(db.Text)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class MedicationRecord(db.Model):
    """用藥紀錄：家長或校務人員建立用藥需求，老師可記錄處理狀態。"""
    __tablename__ = "medication_record"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    student_name = db.Column(db.String(150), nullable=False, index=True)
    student_display = db.Column(db.String(150))
    unit = db.Column(db.String(150), index=True)
    grade = db.Column(db.String(10), index=True)
    class_no = db.Column(db.String(10), index=True)
    medicine_name = db.Column(db.String(200), nullable=False)
    dose = db.Column(db.String(120))
    time_note = db.Column(db.String(200))
    note = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending", index=True)
    teacher_note = db.Column(db.Text)
    created_by = db.Column(db.String(150), nullable=False, index=True)
    created_role = db.Column(db.String(20), nullable=False)
    handled_by = db.Column(db.String(150))
    handled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CareRecord(db.Model):
    """關懷紀錄：老師記錄學生狀況、處理方式與後續追蹤。"""
    __tablename__ = "care_record"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    student_name = db.Column(db.String(150), nullable=False, index=True)
    student_display = db.Column(db.String(150))
    unit = db.Column(db.String(150), index=True)
    grade = db.Column(db.String(10), index=True)
    class_no = db.Column(db.String(10), index=True)
    category = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    observation = db.Column(db.Text)
    action = db.Column(db.Text)
    follow_up_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="open", index=True)
    visible_to_parent = db.Column(db.Integer, default=1)
    visible_to_student = db.Column(db.Integer, default=0)
    created_by = db.Column(db.String(150), nullable=False, index=True)
    updated_by = db.Column(db.String(150))
    updated_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


LEAVE_TYPES = ["事假", "病假", "公假", "其他"]
MEDICATION_STATUS_OPTIONS = {
    "pending": "待處理",
    "done": "已處理",
    "skipped": "未用藥",
}
CARE_CATEGORIES = ["生活適應", "學習狀況", "情緒支持", "同儕互動", "健康安全", "其他"]
CARE_STATUS_OPTIONS = {
    "open": "持續關懷",
    "followed": "已追蹤",
    "closed": "已結案",
}


def _serve_uploaded_file(filename):
    
    fn = _safe_upload_name(filename)
    p_timetable = os.path.join(TIMETABLE_DIR, fn)
    p_root = os.path.join(UPLOAD_DIR, fn)
    if os.path.isfile(p_timetable):
        return send_from_directory(TIMETABLE_DIR, fn)
    if os.path.isfile(p_root):
        return send_from_directory(UPLOAD_DIR, fn)
    from flask import abort
    abort(404)


def _count_words_chars(text: str) -> Tuple[int, int]:
    """
    回傳 (words_count, chars_count)
    - words_count：英文以單字計，中文以字計（\u4e00-\u9fff）。
    - chars_count：移除空白後的總字元數（中英皆計）。
    """
    text = text or ""
    en_tokens = re.findall(r"[A-Za-z0-9_]+", text)
    zh_tokens = re.findall(r"[\u4e00-\u9fff]", text)
    words = len(en_tokens) + len(zh_tokens)
    chars = len(re.sub(r"\s+", "", text, flags=re.U))
    return words, chars


if "uploaded_file" not in app.view_functions:
    app.add_url_rule("/uploads/<path:filename>", endpoint="uploaded_file", view_func=_serve_uploaded_file)


def _req_badges_from_desc(desc: str) -> str:
    kv = kv_parse(desc or "")
    mm = kv_get_int(kv, "MIN_MINUTES", 0)
    mw = kv_get_int(kv, "MIN_WORDS", 0)
    nc = kv_get_int(kv, "NEED_COREAD", 0)
    tp = kv_get_str(kv, "TOPIC", "")
    bs = []
    if tp: bs.append(f"<span class='badge bg-secondary ms-1'>主題：{escape(tp)}</span>")
    if mm > 0: bs.append(f"<span class='badge bg-primary ms-1'>≥{mm} 分</span>")
    if mw > 0: bs.append(f"<span class='badge bg-info ms-1'>心得≥{mw}字</span>")
    if nc:     bs.append(f"<span class='badge bg-warning text-dark ms-1'>需親子共讀</span>")
    return "".join(bs)



BASE_HTML = """<!doctype html><html lang="zh-TW"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="{{ url_for('static', filename='app.css', v=static_version) }}" rel="stylesheet">
</head>
<body class="bg-light">
  <!-- Navbar（右側只保留登入/登出） -->
  <nav class="navbar navbar-expand-md shadow-none app-navbar">
    <div class="container">
      <a class="navbar-brand fw-bold" href="/">永續閱讀電子聯絡簿</a>
      <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#nav" aria-label="切換導覽">
        <span class="navbar-toggler-icon"></span>
      </button>
      <div id="nav" class="collapse navbar-collapse">
        <ul class="navbar-nav ms-auto align-items-md-center">
          {% if current_user.is_authenticated %}
            <li class="nav-item"><a class="btn btn-sm btn-outline-secondary" href="{{ url_for('logout') }}">登出</a></li>
          {% else %}
            <li class="nav-item"><a class="btn btn-sm btn-success" href="{{ url_for('login') }}">登入</a></li>
          {% endif %}
        </ul>
      </div>
    </div>
  </nav>

  <!-- Page header -->
  <header class="app-hero">
    <div class="container py-3">
      <h1 class="page-title">{{ title }}</h1>
    </div>
  </header>

  <!-- Main card -->
  <main class="container py-2">
    <div class="card app-card p-4 {% if request.path == '/login' %}app-card--login{% elif request.path == '/register' %}app-card--register{% endif %}">
      {{ content|safe }}
      {% if links %}
        <div class="text-center mt-3">
          {% for text, href in links %}
            <a class="btn btn-sm btn-outline-secondary m-1" href="{{ href }}">{{ text }}</a>
          {% endfor %}
        </div>
      {% endif %}
      {% if request.path not in ['/', '/login', '/register'] %}
        <div class="text-center mt-3">
          <a class="btn btn-sm btn-secondary" href="javascript:history.back()">返回</a>
        </div>
      {% endif %}
    </div>
  </main>

  <!-- Footer -->
  <footer class="app-footer py-4">
    <div class="container d-flex flex-wrap justify-content-between align-items-center">
      <div class="small text-muted">© {{ year }} 永續閱讀電子聯絡簿</div>
      <div class="small text-muted">國立臺東大學 116級資訊管理學系專題</div>
    </div>
  </footer>

  <!-- Toasts -->
  <div class="toast-container position-fixed top-0 end-0 p-3" id="toast-container"></div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  <script>
  (function(){
    function showToast(level, msg){
      var container = document.getElementById('toast-container');
      var bg = (level==='success')?'bg-success':(level==='warning')?'bg-warning text-dark':(level==='danger')?'bg-danger':'bg-secondary';
      var html = `
      <div class="toast app-toast align-items-center border-0 mb-2" role="status" aria-live="polite" aria-atomic="true" data-bs-delay="3000">
        <div class="toast-header small">
          <span class="badge rounded-pill ${bg} me-2" style="width:.75rem;height:.75rem">&nbsp;</span>
          <strong class="me-auto">提醒</strong>
          <small class="text-muted">現在</small>
          <button type="button" class="btn-close ms-2" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
        <div class="toast-body">${msg}</div>
      </div>`;
      container.insertAdjacentHTML('beforeend', html);
      var toastEl = container.lastElementChild;
      var t = new bootstrap.Toast(toastEl);
      t.show();
    }

    // URL 參數
    var sp = new URLSearchParams(location.search);
    var tl = sp.get('toast_level'), tm = sp.get('toast_msg');
    if(tl && tm){ try{ showToast(tl, decodeURIComponent(tm)); }catch(e){ showToast(tl, tm); } }

    // page() 傳入 msg
    {% if msg %}
      showToast('info', {{ msg|tojson }});
    {% endif %}

    // Flask flash()（若有）
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, message in messages %}
          showToast({{ cat|tojson }}, {{ message|tojson }});
        {% endfor %}
      {% endif %}
    {% endwith %}

    // 捲動陰影
    document.addEventListener('scroll', function(){
      document.body.classList.toggle('scrolled', window.scrollY > 4);
    });

    // 自動將含日期欄位的表單內「前往」按鈕加寬
    document.querySelectorAll('form').forEach(function(f){
      if (!f.querySelector('input[type="date"]')) return;
      var submits = Array.from(f.querySelectorAll('button, input[type="submit"]'));
      submits.forEach(function(el){
        var label = (el.tagName === 'INPUT' ? el.value : el.textContent).trim();
        if (label === '前往') el.classList.add('btn-wide');
      });
    });
  })();
  </script>
</body></html>"""



@login_manager.user_loader
def load_user(user_id: str):
    
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None



def local_now(hours: int = 8):
    """
    回傳「現在」時間，已改為使用伺服器當地時間。
    hours 參數僅保留相容性，實際不再使用。
    """
    return datetime.now()

def local_today(hours: int = 8):
    """
    回傳今天日期（依伺服器當地時間）。
    hours 參數僅保留相容性，實際不再使用。
    """
    return local_now().date()

def today():
    """
    專案統一使用的今日日期，請改用此函式，而不要再直接用 datetime.utcnow().date()。
    """
    return local_today()


def is_homeroom_of(user: User, grade: str, class_no: str) -> bool:
    if not user or user.role != "teacher":
        return False
    return (
        str(user.is_homeroom or 0) == "1"
        and str(user.grade or "") == str(grade)
        and str(user.class_no or "") == str(class_no)
    )


def shortcut_buttons_html():
    
    try:
        assign_url = url_for("teacher_assignments_page")   
    except Exception:
        try:
            assign_url = url_for("assign_teachers")        
        except Exception:
            assign_url = "/teacher_assignments"            
    return (
        "<div class='d-flex flex-wrap gap-2 mb-3'>"
        "<a class='btn btn-outline-secondary btn-sm' href='/profile'>個人檔案</a>"
        "</div>"
    )

def can_manage_task(user, task: "Task") -> bool:
    """
    只要是自己出的作業就能編輯/刪除；leader/admin 也能。
    其餘沿用你原本的授課授權邏輯（can_post_task）。
    """
    if not user or not task:
        return False
    if user.role in ("leader", "admin"):
        return True
    if task.created_by == user.username:
        return True
    
    subj = task.category or task.mission_category
    return can_post_task(user, subj, task.grade, task.class_no)


def teacher_assignments(teacher_username: str):
    """給 /teacher 用：回傳 (subject, grade, class_no) 列表"""
    rows = db.session.execute(text("""
        SELECT subject, grade, class_no
          FROM teaching_assignment
         WHERE teacher_username = :t
         ORDER BY grade, class_no, subject
    """), {"t": teacher_username}).mappings().all()
    return [(r["subject"], r["grade"], r["class_no"]) for r in rows]

def teacher_assignments_full(teacher_username: str):
    """給 /teacher_assignments 頁用：回傳 (id, subject, grade, class_no) 列表"""
    rows = db.session.execute(text("""
        SELECT id, subject, grade, class_no
          FROM teaching_assignment
         WHERE teacher_username = :t
         ORDER BY grade, class_no, subject
    """), {"t": teacher_username}).mappings().all()
    return [(r["id"], r["subject"], r["grade"], r["class_no"]) for r in rows]

def can_post_task(user: User, subject: str, grade: str, class_no: str) -> bool:
    """老師是否能對指定(科目,年級,班級)發佈作業。"""
    if not user or user.role not in ("teacher", "leader", "admin"):
        return False
    if user.role in ("leader", "admin"):
        return True
    
    if is_homeroom_of(user, grade, class_no):
        return True

    subject_set = {str(subject or "").strip()}
    subject_set |= SUBJECT_ALIASES.get(str(subject or "").strip(), set())
    rows = db.session.execute(text("""
        SELECT subject FROM teaching_assignment
         WHERE teacher_username=:t AND grade=:g AND class_no=:c
    """), {"t": user.username, "g": grade, "c": class_no}).mappings().all()
    for row in rows:
        assigned = str(row.get("subject") or "").strip()
        assigned_set = {assigned} | SUBJECT_ALIASES.get(assigned, set())
        if subject_set & assigned_set:
            return True
    return False


def _has_teaching_assignment(user: "User", subject: str, grade: str, class_no: str) -> bool:
    if not user or getattr(user, "role", None) != "teacher":
        return False
    row = db.session.execute(text("""
        SELECT 1 FROM teaching_assignment
         WHERE teacher_username=:t AND subject=:s AND grade=:g AND class_no=:c
         LIMIT 1
    """), {
        "t": user.username,
        "s": subject,
        "g": str(grade),
        "c": str(class_no),
    }).first()
    return row is not None


def can_manage_reading_class(user: "User", grade: str, class_no: str) -> bool:
    """閱讀任務以班級為單位；教師限班導或閱讀授課教師。"""
    if not user or not grade or not class_no:
        return False
    if getattr(user, "role", None) in ("leader", "admin"):
        return True
    if getattr(user, "role", None) != "teacher":
        return False
    return (
        is_homeroom_of(user, grade, class_no)
        or _has_teaching_assignment(user, READING_SUBJECT, grade, class_no)
    )

def add_watermark_text(abs_path: str, text: str):
    """
    在簽名 PNG 右下角加上半透明白底＋深色文字的浮水印（日期、學童名）。
    abs_path：檔案的絕對路徑。
    """
    try:
        im = Image.open(abs_path).convert("RGBA")
        W, H = im.size
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = ImageFont.load_default()
        pad = 10
        tw, th = draw.textbbox((0, 0), text, font=font)[2:]
        box_w = tw + pad * 2
        box_h = th + pad * 2
        x0 = W - box_w - 8
        y0 = H - box_h - 8
        draw.rectangle([x0, y0, x0 + box_w, y0 + box_h], fill=(255, 255, 255, 180))
        draw.text((x0 + pad, y0 + pad), text, fill=(17, 24, 39, 255), font=font)
        out = Image.alpha_composite(im, overlay).convert("RGB")
        out.save(abs_path, format="PNG")
    except Exception:
        pass

def _sign_form_html(sid: str, scope: str, d, msg: str = "") -> str:
    title = "日記" if scope == "diary" else "作業與日記"
    stu = User.query.filter_by(username=sid, role="student").first()
    student_label = (stu.display_name or stu.username) if stu else sid
    class_label = ""
    if stu:
        class_label = " · ".join([x for x in [stu.unit, f"{stu.grade}年{stu.class_no}班" if stu.grade or stu.class_no else ""] if x])
    m = f"<div class='alert alert-warning'>{msg}</div>" if msg else ""
    return (
        m + f"""
        <style>
        .sign-page{{display:flex;flex-direction:column;gap:1rem;max-width:880px;margin:0 auto;}}
        .sign-hero{{border-radius:28px;padding:1.35rem;background:linear-gradient(135deg,#ecfdf3 0%,#fff8e8 58%,#eef7ff 100%);border:1px solid rgba(35,92,59,.1);box-shadow:0 22px 54px rgba(24,76,46,.1);}}
        .sign-kicker{{font-size:.78rem;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#4f8f61;margin-bottom:.25rem;}}
        .sign-hero h3{{margin:0;color:#162318;font-weight:950;letter-spacing:-.02em;}}
        .sign-muted{{color:#647067;font-weight:800;margin-top:.3rem;}}
        .sign-card{{border-radius:26px;background:#fff;border:1px solid rgba(19,56,35,.08);box-shadow:0 20px 48px rgba(24,76,46,.1);padding:1.15rem;}}
        .sign-pad-wrap{{border-radius:22px;background:#f8fbf6;border:1px solid rgba(35,92,59,.08);padding:1rem;}}
        #pad{{width:100%;max-width:100%;height:260px;border:2px dashed rgba(35,92,59,.22);border-radius:18px;background:#fff;touch-action:none;}}
        .sign-actions{{display:flex;gap:.55rem;flex-wrap:wrap;align-items:center;margin-top:.8rem;}}
        .sign-actions .btn{{border-radius:999px;font-weight:850;}}
        </style>
        <div class='sign-page'>
          <section class='sign-hero'>
            <div class='sign-kicker'>Parent Signature</div>
            <h3>為 {escape(student_label)} 簽名</h3>
            <div class='sign-muted'>{escape(class_label) if class_label else '家長簽閱'} · {d} · {title}</div>
          </section>
          <section class='sign-card'>
            <div class='mb-3'>
              <div class='fw-bold mb-1'>請在下方簽名框簽名</div>
              <div class='text-muted'>可使用滑鼠、觸控板或手指書寫；送出後老師端會看到簽名紀錄。</div>
            </div>
        """ +
        "<form method='post' onsubmit='return submitSign()'>"
        f"<input type='hidden' name='student' value='{sid}'>"
        f"<input type='hidden' name='date' value='{d}'>"
        f"<input type='hidden' name='scope' value='{scope}'>"
        "<input type='hidden' name='image_data' id='image_data'>"
        "<div class='sign-pad-wrap mb-3'><canvas id='pad'></canvas></div>"
        "<div class='sign-actions mb-3'>"
        "<button type='button' class='btn btn-outline-secondary' onclick='clearPad()'>清除</button>"
        "<button type='button' class='btn btn-outline-dark' onclick='undoPad()'>復原</button>"
        "</div>"
        "<div class='mb-3'><label class='form-label'>備註（可留空）</label>"
        "<input class='form-control' name='note' placeholder='例如：已確認閱讀與完成作業'></div>"
        "<button class='btn btn成功 btn-success'>送出簽名</button> "
        "<a class='btn btn-outline-secondary' href='javascript:history.back()'>返回</a>"
        "</form>"
        "</section></div>"
        """
        <script>
        // 簡易簽名板（不依賴外部套件）
        const pad = document.getElementById('pad');
        const ctx = pad.getContext('2d');
        let drawing = false, pts = [];

        function resize(){ const r = pad.getBoundingClientRect(); pad.width = r.width; pad.height = 260; redraw(); }
        function redraw(){
          ctx.fillStyle = '#ffffff'; ctx.fillRect(0,0,pad.width,pad.height);
          ctx.lineWidth = 2.2; ctx.lineCap='round'; ctx.strokeStyle = '#111827';
          ctx.beginPath();
          for (let i=0;i<pts.length;i++){
            const p = pts[i];
            if (p.move) ctx.moveTo(p.x,p.y); else ctx.lineTo(p.x,p.y);
          }
          ctx.stroke();
        }
        function pos(e){
          const r = pad.getBoundingClientRect();
          const x = (e.touches? e.touches[0].clientX : e.clientX) - r.left;
          const y = (e.touches? e.touches[0].clientY : e.clientY) - r.top;
          return {x, y};
        }
        function down(e){ e.preventDefault(); drawing=true; const p=pos(e); pts.push({x:p.x,y:p.y,move:true}); redraw(); }
        function move(e){ if(!drawing) return; const p=pos(e); pts.push({x:p.x,y:p.y,move:false}); redraw(); }
        function up(e){ drawing=false; }
        function clearPad(){ pts=[]; redraw(); }
        function undoPad(){
          // 復原到上一段筆劃（找到上一個 move:true）
          for (let i=pts.length-1;i>=0;i--){ if(pts[i].move){ pts = pts.slice(0,i); break; } }
          redraw();
        }
        function submitSign(){
          if(pts.length<2){ alert('請先簽名'); return false; }
          document.getElementById('image_data').value = pad.toDataURL('image/png');
          return true;
        }
        window.addEventListener('resize', resize);
        ['mousedown','touchstart'].forEach(evt=>pad.addEventListener(evt,down,{passive:false}));
        ['mousemove','touchmove'].forEach(evt=>pad.addEventListener(evt,move,{passive:false}));
        ['mouseup','mouseleave','touchend','touchcancel'].forEach(evt=>pad.addEventListener(evt,up,{passive:false}));
        resize();
        </script>
        """
    ).replace("btn成功", "btn")  

def is_mission(t: "Task") -> bool:
    return getattr(t, "task_type", "homework") == "mission"

def mission_badge(t: "Task") -> str:
    
    return " <span class='badge bg-dark ms-1'>永續閱讀任務</span>" if is_mission(t) else ""

def save_dataurl_png(data_url: str) -> str | None:
    """接收 data:image/png;base64,... 轉檔存檔，回傳檔名。"""
    if not (data_url and data_url.startswith("data:image/png;base64,")):
        return None
    try:
        b64 = data_url.split(",", 1)[1]
        raw = base64.b64decode(b64)
        uid = f"{uuid.uuid4().hex}_sign.png"
        with open(os.path.join(app.config["UPLOAD_FOLDER"], uid), "wb") as f:
            f.write(raw)
        return uid
    except Exception:
        return None

def _parent_identity_names(user=None) -> list[str]:
    """取得家長帳號的所有可用辨識名稱（帳號 + 顯示名稱，皆轉成小寫去重）。"""
    u = user or current_user
    names = []
    for raw in (getattr(u, "username", None), getattr(u, "display_name", None)):
        if isinstance(raw, str):
            val = raw.strip()
            if val:
                names.append(val.lower())
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return seen

def parent_child_query(user=None, student: str | None = None):
    """統一處理家長與學童綁定查詢，允許帳號或顯示名稱大小寫差異。"""
    keys = _parent_identity_names(user)
    if not keys:
        return ParentChild.query.filter(text("0=1"))
    q = ParentChild.query.filter(func.lower(ParentChild.parent_name).in_(keys))
    if student:
        q = q.filter(ParentChild.student_name == student)
    return q

def parent_child_links(user=None):
    return parent_child_query(user).all()

def parent_child_student_usernames(user=None) -> list[str]:
    return [lk.student_name for lk in parent_child_links(user)]

def signature_of(parent: str, student: str, d, scope: str | None):
    q = ParentSignature.query.filter_by(parent_name=parent, student_name=student, date=d)
    if scope in ("homework", "diary"):
        q = q.filter(ParentSignature.scope.in_(("homework", "diary")))
    elif scope:
        q = q.filter(ParentSignature.scope == scope)
    return q.order_by(ParentSignature.id.desc()).first()

def parent_invite_code(student_username: str) -> str:
    """產生家長邀請碼（學號×13 + 13），僅限學號為純數字。"""
    if not student_username or not student_username.isdigit():
        return ""
    return str(int(student_username) * 13 + 13)

def cat_label(t: "Task") -> str:
    """顯示對應的分類名稱：作業→category；任務→mission_category"""
    if is_mission(t):
        return t.mission_category or "其他"
    return t.category or "其他"

def subj_order() -> list[str]:
    preferred = PREFERRED_SUBJECT_ORDER
    return [s for s in preferred if s in CATEGORY_OPTIONS] + [
        c for c in CATEGORY_OPTIONS if c not in preferred
    ]

def mission_order() -> list[str]:
    pref = ["節能", "節水", "低碳交通", "綠色飲食", "資源回收", "綠美化"]
    return [s for s in pref if s in MISSION_CATEGORY_OPTIONS] + [
        c for c in MISSION_CATEGORY_OPTIONS if c not in pref
    ]

def toast_url(endpoint: str, msg: str, level: str = "info", **params):
    q = dict(params)
    q["toast_level"] = level
    q["toast_msg"] = msg
    return url_for(endpoint, **q)

def toast_redirect(endpoint: str, msg: str, level: str = "info", **params):
    return redirect(toast_url(endpoint, msg, level, **params))

def page(title, content, *, msg="", links=None):
    return render_template_string(
        BASE_HTML,
        title=title,
        content=content,
        msg=msg,
        links=links or [],
        year=local_today().year,   
        static_version=STATIC_VERSION,
    )

def setting_checkbox_html(
    input_id: str,
    name: str,
    title: str,
    hint: str,
    *,
    checked: bool = False,
) -> str:
    checked_attr = " checked" if checked else ""
    return (
        f"<label class='setting-check' for='{escape(input_id)}'>"
        f"<input class='form-check-input setting-check__input' type='checkbox' "
        f"id='{escape(input_id)}' name='{escape(name)}' value='1'{checked_attr}>"
        "<span class='setting-check__copy'>"
        f"<span class='setting-check__title'>{escape(title)}</span>"
        f"<span class='setting-check__hint'>{escape(hint)}</span>"
        "</span>"
        "</label>"
    )

def role_endpoint(role: str) -> str:
    return {
        "student": "homework",
        "teacher": "teacher",
        "parent": "parent",
        "leader": "leader",
    }.get(role, "home")


def role_quick_actions_html(user=None, include_profile: bool = True, extra_class: str = "mb-3") -> str:
    user = user or current_user

    def link(label: str, href: str, style: str = "outline-secondary") -> str:
        return f"<a class='btn btn-sm btn-{style}' href='{href}'>{label}</a>"

    role = getattr(user, "role", "")
    actions = []
    if include_profile:
        actions.append(("個人檔案", "/profile", "outline-secondary"))

    if role == "student":
        actions += [
            ("聯絡簿", "/homework", "outline-primary"),
            ("師生交流", "/comm", "outline-secondary"),
            ("用藥紀錄", "/medication", "outline-secondary"),
            ("我的假單", "/leave/mine", "outline-secondary"),
        ]
    elif role == "parent":
        actions += [
            ("家長首頁", "/parent", "outline-primary"),
            ("聯絡簿", "/homework", "outline-secondary"),
            ("日記與簽名", "/parent#parent-diary-panel", "outline-primary"),
            ("親師交流", "/comm", "outline-secondary"),
            ("用藥紀錄", "/medication", "outline-secondary"),
        ]
    elif role == "teacher":
        actions += [
            ("老師工作區", "/teacher", "outline-primary"),
            ("日記題目", "/teacher_diary", "outline-secondary"),
            ("親師交流", "/comm", "outline-secondary"),
            ("請假審核", "/leave/manage", "outline-secondary"),
            ("用藥紀錄", "/medication", "outline-secondary"),
        ]
    elif role == "leader":
        actions += [
            ("組長中心", "/leader", "outline-primary"),
            ("學期週次", "/semester", "outline-success"),
            ("課表圖片", "/timetable/images", "outline-secondary"),
            ("閱讀審核", "/reading/review", "outline-success"),
            ("請假審核", "/leave/manage", "outline-secondary"),
            ("用藥紀錄", "/medication", "outline-secondary"),
        ]
    return "<div class='role-action-bar d-flex flex-wrap gap-2 " + extra_class + "'>" + "".join(
        link(label, href, style) for label, href, style in actions
    ) + "</div>"


def parse_date(s: str | None):
    if not s:
        return None
    s = s.strip().replace("/", "-")
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None

def _week_spans(sem: Semester):
    spans = []
    cur = sem.start_date - timedelta(days=sem.start_date.weekday())  
    idx = 1
    while cur <= sem.end_date:
        s = cur
        e = min(cur + timedelta(days=6), sem.end_date)
        spans.append((idx, s, e))
        cur = e + timedelta(days=1)
        idx += 1
    return spans

def current_semester(unit: str, d=None) -> Semester | None:
    d = d or today()
    return (
        Semester.query.filter_by(unit=unit)
        .filter(Semester.start_date <= d, Semester.end_date >= d)
        .order_by(Semester.start_date.desc())
        .first()
    )

def semester_of_date(unit: str, d=None):
    """回傳 (學期物件, 週次 int或None)"""
    d = d or today()
    sem = current_semester(unit, d)
    if not sem:
        return None, None
    wk = (
        SemesterWeek.query.filter_by(semester_id=sem.id)
        .filter(SemesterWeek.start_date <= d, SemesterWeek.end_date >= d)
        .first()
    )
    if wk:
        return sem, int(wk.week_no)
    
    start_mon = sem.start_date - timedelta(days=sem.start_date.weekday())
    week_no = ((d - start_mon).days // 7) + 1
    return sem, max(1, week_no)


def _teacher_scopes(user: "User") -> set[tuple[str, str]]:
    if not user or user.role not in ("teacher", "leader", "admin"):
        return set()
    rows = TeachingAssignment.query.filter_by(teacher_username=user.username).all()
    return {(str(r.grade), str(r.class_no)) for r in rows}

def _can_edit_class(user: "User", grade: str, class_no: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.role in ("leader", "admin"):
        return True
    if is_homeroom_of(user, grade, class_no):
        return True
    return user.role == "teacher" and (str(grade), str(class_no)) in _teacher_scopes(user)


def _ext_ok(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXTS

def _save_images(files, subdir="timetable"):
    saved = []
    if subdir == "timetable":
        base = TIMETABLE_DIR
    elif subdir == "semester":
        base = SEMESTER_DIR
    else:
        base = UPLOAD_DIR
    for f in files:
        if not f or not getattr(f, "filename", ""):
            continue
        if not _ext_ok(f.filename):
            continue
        ext = f.filename.rsplit(".", 1)[1].lower()
        name = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(base, name))
        saved.append(name)
    return saved

def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXTS

def save_image(fs):
    if not fs or not fs.filename.strip() or not allowed_file(fs.filename):
        return None
    name = secure_filename(_safe_upload_name(fs.filename))
    uid = f"{uuid.uuid4().hex}_{name}"
    fs.save(os.path.join(app.config["UPLOAD_FOLDER"], uid))
    return uid


def _serve_uploaded_file_unified(filename):
    import os
    from flask import abort, send_from_directory

    fn = _safe_upload_name(filename)
    candidates = []
    try:
        candidates.append((SEMESTER_DIR, os.path.join(SEMESTER_DIR, fn)))
    except NameError:
        pass
    try:
        candidates.append((TIMETABLE_DIR, os.path.join(TIMETABLE_DIR, fn)))
    except NameError:
        pass
    candidates.append((UPLOAD_DIR, os.path.join(UPLOAD_DIR, fn)))
    for base, path in candidates:
        if os.path.isfile(path):
            return send_from_directory(base, fn)
    abort(404)


if "uploaded_file" not in app.view_functions:
    app.add_url_rule(
        "/uploads/<path:filename>",
        endpoint="uploaded_file",
        view_func=_serve_uploaded_file_unified,
        methods=["GET"],
    )
else:
    app.view_functions["uploaded_file"] = _serve_uploaded_file_unified

def display_name_of(acc):
    u = User.query.filter_by(username=acc).first()
    return (u.display_name or u.username) if u else acc

def status_badge(t):
    return (
        "<span class='badge bg-secondary ms-1'>已截止</span>"
        if (t.end_date and today() > t.end_date)
        else "<span class='badge bg-primary ms-1'>進行中</span>"
    )

def require_submit_badge(t):
    """只有需要繳交時才顯示徽章；預設只讀不顯示任何提示"""
    return (
        " <span class='badge bg-primary ms-1'>需要繳交</span>"
        if getattr(t, "is_view_only", 1) == 0
        else ""
    )

def total_points_of(student):
    q = (
        db.session.query(
            func.coalesce(
                func.sum(func.coalesce(CompletedTask.calc_points, Task.points, 0)), 0
            )
        )
        .select_from(CompletedTask)
        .join(Task, CompletedTask.task_id == Task.id)
        .filter(CompletedTask.student_name == student, CompletedTask.approved == 1)
    )
    return int(q.scalar() or 0)

def compute_calc_points(completed: CompletedTask, task: Task) -> int:
    """
    核准時計算最終積分：
    基礎分 + 心得加分(+3) + 照片加分(+2) + 三日連續加成(+10)
    """
    base = int(task.points or 0)
    bonus_reflect = 3 if completed.has_reflection else 0
    bonus_image = 2 if completed.has_image else 0

    
    d = (completed.created_at or local_now()).date()

    def has_approved_on(day):
        return (
            db.session.query(CompletedTask.id)
            .filter_by(student_name=completed.student_name, approved=1)
            .filter(func.date(CompletedTask.approved_at) == day)
            .first()
            is not None
        )

    streak_bonus = (
        10
        if (has_approved_on(d - timedelta(days=1)) and has_approved_on(d - timedelta(days=2)))
        else 0
    )
    completed.streak3_bonus = streak_bonus
    return base + bonus_reflect + bonus_image + streak_bonus

def latest_status_for(student_username, task_id):
    c = (
        CompletedTask.query.filter_by(student_name=student_username, task_id=task_id)
        .order_by(CompletedTask.id.desc())
        .first()
    )
    if not c:
        return "none", None
    if c.approved is None:
        return "pending", c
    if c.approved == 1:
        return "approved", c
    return "rejected", c

def scope_txt(t: "Task"):
    return (
        "全校"
        if t.is_school_wide
        else (
            f"{t.grade}年{t.class_no}班"
            if t.class_no
            else (f"{t.grade}年級" if t.grade else "（未設）")
        )
    )

def opt(vals, sel=""):
    return "".join(
        f"<option value='{v}' {'selected' if str(v)==str(sel) else ''}>{v}</option>"
        for v in vals
    )

def g(name, default=""):
    return (request.form.get(name) or default).strip()


def ensure_columns():
    with db.engine.begin() as c:
        
        def cols(table: str):
            rows = c.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return {str(r[1]).lower() for r in rows}

        
        def addcol(table: str, col: str, ddl: str):
            if col.lower() in cols(table):
                return
            try:
                c.execute(text(ddl))
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    raise

        
        try:
            c.execute(text("SELECT 1 FROM teaching_assignment LIMIT 1"))
        except Exception:
            c.execute(text("""
                CREATE TABLE IF NOT EXISTS teaching_assignment (
                    id INTEGER PRIMARY KEY,
                    teacher_username VARCHAR(150) NOT NULL,
                    subject VARCHAR(100) NOT NULL,
                    grade VARCHAR(10) NOT NULL,
                    class_no VARCHAR(10) NOT NULL,
                    created_at DATETIME,
                    CONSTRAINT uq_teacher_subject_class
                        UNIQUE (teacher_username, subject, grade, class_no)
                )
            """))
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_ta_teacher ON teaching_assignment(teacher_username)"))

        
        addcol("user", "display_name", "ALTER TABLE user ADD COLUMN display_name VARCHAR(150)")
        addcol("user", "unit",         "ALTER TABLE user ADD COLUMN unit VARCHAR(150)")
        addcol("user", "grade",        "ALTER TABLE user ADD COLUMN grade VARCHAR(10)")
        addcol("user", "class_no",     "ALTER TABLE user ADD COLUMN class_no VARCHAR(10)")
        addcol("user", "is_homeroom",  "ALTER TABLE user ADD COLUMN is_homeroom INTEGER DEFAULT 0")
        addcol("user", "email",        "ALTER TABLE user ADD COLUMN email VARCHAR(150)")
        addcol("user", "phone",        "ALTER TABLE user ADD COLUMN phone VARCHAR(50)")
        addcol("user", "line_id",      "ALTER TABLE user ADD COLUMN line_id VARCHAR(100)")
        addcol("user", "title",        "ALTER TABLE user ADD COLUMN title VARCHAR(100)")
        addcol("user", "subjects",     "ALTER TABLE user ADD COLUMN subjects TEXT")
        addcol("user", "bio",          "ALTER TABLE user ADD COLUMN bio TEXT")
        addcol("user", "office_hours", "ALTER TABLE user ADD COLUMN office_hours VARCHAR(200)")
        addcol("user", "contact_pref", "ALTER TABLE user ADD COLUMN contact_pref VARCHAR(20)")
        addcol("user", "avatar",       "ALTER TABLE user ADD COLUMN avatar VARCHAR(255)")
        c.execute(text("UPDATE user SET display_name=COALESCE(display_name,username)"))
        c.execute(text("UPDATE user SET unit = COALESCE(NULLIF(TRIM(unit), ''), :school)"), {"school": SCHOOL_NAME})
        c.execute(text("""
            UPDATE user
               SET is_homeroom = 1
             WHERE role='teacher'
               AND COALESCE(TRIM(grade),'') <> ''
               AND COALESCE(TRIM(class_no),'') <> ''
        """))

        
        try:
            c.execute(text("SELECT 1 FROM comm_note LIMIT 1"))
            addcol("comm_note", "author_role",        "ALTER TABLE comm_note ADD COLUMN author_role VARCHAR(20)")
            addcol("comm_note", "visible_to_parent",  "ALTER TABLE comm_note ADD COLUMN visible_to_parent INTEGER DEFAULT 1")
            addcol("comm_note", "visible_to_student", "ALTER TABLE comm_note ADD COLUMN visible_to_student INTEGER DEFAULT 1")
            addcol("comm_note", "student_username",   "ALTER TABLE comm_note ADD COLUMN student_username VARCHAR(150)")
        except Exception:
            pass

        c.execute(text("""
            CREATE TABLE IF NOT EXISTS medication_record (
                id INTEGER PRIMARY KEY,
                date DATE NOT NULL,
                student_name VARCHAR(150) NOT NULL,
                student_display VARCHAR(150),
                unit VARCHAR(150),
                grade VARCHAR(10),
                class_no VARCHAR(10),
                medicine_name VARCHAR(200) NOT NULL,
                dose VARCHAR(120),
                time_note VARCHAR(200),
                note TEXT,
                status VARCHAR(20) DEFAULT 'pending',
                teacher_note TEXT,
                created_by VARCHAR(150) NOT NULL,
                created_role VARCHAR(20) NOT NULL,
                handled_by VARCHAR(150),
                handled_at DATETIME,
                created_at DATETIME
            )
        """))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_med_date_class ON medication_record(date, unit, grade, class_no)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_med_student ON medication_record(student_name)"))

        c.execute(text("""
            CREATE TABLE IF NOT EXISTS care_record (
                id INTEGER PRIMARY KEY,
                date DATE NOT NULL,
                student_name VARCHAR(150) NOT NULL,
                student_display VARCHAR(150),
                unit VARCHAR(150),
                grade VARCHAR(10),
                class_no VARCHAR(10),
                category VARCHAR(50) NOT NULL,
                title VARCHAR(200) NOT NULL,
                observation TEXT,
                action TEXT,
                follow_up_date DATE,
                status VARCHAR(20) DEFAULT 'open',
                visible_to_parent INTEGER DEFAULT 1,
                visible_to_student INTEGER DEFAULT 0,
                created_by VARCHAR(150) NOT NULL,
                updated_by VARCHAR(150),
                updated_at DATETIME,
                created_at DATETIME
            )
        """))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_care_date_class ON care_record(date, unit, grade, class_no)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_care_student ON care_record(student_name)"))

        
        addcol("task", "category",         "ALTER TABLE task ADD COLUMN category VARCHAR(100)")
        addcol("task", "mission_category", "ALTER TABLE task ADD COLUMN mission_category VARCHAR(100)")
        addcol("task", "points",           "ALTER TABLE task ADD COLUMN points INTEGER")
        addcol("task", "start_date",       "ALTER TABLE task ADD COLUMN start_date DATE")
        addcol("task", "end_date",         "ALTER TABLE task ADD COLUMN end_date DATE")
        addcol("task", "unit",             "ALTER TABLE task ADD COLUMN unit VARCHAR(150)")
        addcol("task", "grade",            "ALTER TABLE task ADD COLUMN grade VARCHAR(10)")
        addcol("task", "class_no",         "ALTER TABLE task ADD COLUMN class_no VARCHAR(10)")
        addcol("task", "is_school_wide",   "ALTER TABLE task ADD COLUMN is_school_wide INTEGER DEFAULT 0")
        addcol("task", "is_view_only",     "ALTER TABLE task ADD COLUMN is_view_only INTEGER DEFAULT 0")
        addcol("task", "task_type",        "ALTER TABLE task ADD COLUMN task_type VARCHAR(20) DEFAULT 'homework'")
        addcol("task", "image",            "ALTER TABLE task ADD COLUMN image VARCHAR(255)")
        addcol("diary_prompt", "image",    "ALTER TABLE diary_prompt ADD COLUMN image VARCHAR(255)")
        c.execute(text("UPDATE task SET category=COALESCE(category,'其他')"))
        c.execute(text("UPDATE task SET points=COALESCE(points,0)"))
        c.execute(text("UPDATE task SET task_type=COALESCE(task_type,'homework')"))
        c.execute(text("UPDATE task SET is_view_only=1 WHERE COALESCE(task_type,'homework')='homework'"))
        c.execute(text("UPDATE task SET is_view_only=0 WHERE COALESCE(task_type,'homework')='mission'"))
        c.execute(text("""
            UPDATE task
               SET mission_category = '其他'
             WHERE COALESCE(task_type,'homework')='mission'
               AND (mission_category IS NULL OR TRIM(mission_category)='')
        """))
        c.execute(text("UPDATE task SET unit = COALESCE(NULLIF(TRIM(unit), ''), :school)"), {"school": SCHOOL_NAME})

        
        addcol("completed_task","proof_image",    "ALTER TABLE completed_task ADD COLUMN proof_image VARCHAR(255)")
        addcol("completed_task","reflection",     "ALTER TABLE completed_task ADD COLUMN reflection TEXT")
        addcol("completed_task","created_at",     "ALTER TABLE completed_task ADD COLUMN created_at DATETIME")
        addcol("completed_task","approved",       "ALTER TABLE completed_task ADD COLUMN approved INTEGER")
        addcol("completed_task","approved_by",    "ALTER TABLE completed_task ADD COLUMN approved_by VARCHAR(150)")
        addcol("completed_task","approved_at",    "ALTER TABLE completed_task ADD COLUMN approved_at DATETIME")
        addcol("completed_task","reject_reason",  "ALTER TABLE completed_task ADD COLUMN reject_reason TEXT")
        addcol("completed_task","with_parent",    "ALTER TABLE completed_task ADD COLUMN with_parent INTEGER DEFAULT 0")
        addcol("completed_task","has_reflection", "ALTER TABLE completed_task ADD COLUMN has_reflection INTEGER DEFAULT 0")
        addcol("completed_task","has_image",      "ALTER TABLE completed_task ADD COLUMN has_image INTEGER DEFAULT 0")
        addcol("completed_task","calc_points",    "ALTER TABLE completed_task ADD COLUMN calc_points INTEGER")
        addcol("completed_task","streak3_bonus",  "ALTER TABLE completed_task ADD COLUMN streak3_bonus INTEGER DEFAULT 0")

@app.before_request
def _ensure():
    try:
        ensure_columns()
    except Exception:
        pass

@login_manager.unauthorized_handler
def _unauth():
    return redirect(url_for("login"))


@app.route("/")
def home():
    return page("首頁", render_home_content())


def render_home_content():
    if current_user.is_authenticated:
        return (
            "<div class='text-center py-3'>"
            "<p class='text-muted mb-3'>你已登入，可以回到自己的工作區，或直接登出。</p>"
            f"<a class='btn btn-success me-2' href='{url_for(role_endpoint(current_user.role))}'>進入工作區</a>"
            "<a class='btn btn-outline-secondary' href='/logout'>登出</a>"
            "</div>"
        )
    return (
        "<div class='text-center py-3'>"
        "<p><a class='btn btn-primary' href='/login'>登入</a></p>"
        "<p class='text-muted mb-0'>沒有帳號？請在登入頁點擊「註冊新帳號」。</p>"
        "</div>"
    )

def render_login_form():
    return (
        "<div class='auth-simple auth-simple--login'>"
        "<section class='auth-card'>"
        "<div class='auth-card__head'>"
        "<div><div class='auth-eyebrow'>登入</div><h2>歡迎回來</h2></div>"
        "</div>"
        "<form method='post'>"
        "<div class='mb-3'>"
        "<label class='form-label'>帳號</label>"
        "<input name='username' class='form-control' placeholder='請輸入帳號' required pattern='[A-Za-z0-9]+'>"
        "</div>"
        "<div class='mb-3'>"
        "<label class='form-label'>密碼</label>"
        "<input type='password' name='password' class='form-control' placeholder='請輸入密碼' required>"
        "</div>"
        "<button class='btn btn-success w-100'>登入</button>"
        "<div class='auth-links'>"
        "<span>第一次使用？</span><a href='/register'>註冊新帳號</a>"
        "</div>"
        "</form>"
        "</section>"
        "</div>"
    )

def _handle_login_submit():
    username = g("username")
    password = g("password")

    u = User.query.filter_by(username=username, password=password).first()
    if u:
        if getattr(u, "role", None) == "admin" or username == "admin":
            return page("登入", render_login_form(), msg="前台已不提供管理員登入，請使用後台管理系統。")
        if not u.unit:
            u.unit = SCHOOL_NAME
            db.session.commit()
        login_user(u)
        return redirect(url_for(role_endpoint(u.role)))

    return page("登入", render_login_form(), msg="帳號或密碼錯誤。")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        return _handle_login_submit()
    return page("登入", render_login_form())



def valid_staff_invite(role: str, code: str) -> bool:
    if not (code and code.isdigit() and len(code) == 10):
        return False
    n = int(code)
    return (n % 23 == 0) if role == "leader" else (n % 13 == 0) if role == "teacher" else False

def parent_code_to_sid(code: str):
    if not (code and code.isdigit()):
        return None
    n = int(code)
    return str((n - 13) // 13) if (n - 13) % 13 == 0 else None

@app.route("/register", methods=["GET","POST"])
def register():
    from flask import request
    g = request.form.get

    msg = ""
    if request.method == "POST":
        display, username, role = g("display_name"), g("username"), g("role")
        pw, pw2 = g("password"), g("password2")
        grade, class_no = g("grade"), g("class_no")
        invite, parent_code = g("invite"), g("parent_code")
        is_hm_flag = 1 if request.form.get("is_homeroom") else 0

        
        ta_subjects = request.form.getlist("ta_subject[]")
        ta_grades   = request.form.getlist("ta_grade[]")
        ta_classes  = request.form.getlist("ta_class[]")

        if role not in ("teacher", "parent", "leader"):
            msg = "僅能註冊 老師/家長/組長。"
        elif not all([display, username, pw, pw2]):
            msg = "請完整填寫帳號、名稱與密碼。"
        elif not ACCOUNT_RE.match(username):
            msg = "帳號僅能英數字。"
        elif User.query.filter_by(username=username).first():
            msg = "帳號已存在。"
        elif pw != pw2:
            msg = "兩次密碼不一致。"
        elif role in ("teacher", "leader"):
            if not valid_staff_invite(role, invite):
                msg = "邀請碼不正確。"
            else:
                
                u = User(
                    username=username,
                    display_name=display,
                    password=pw,
                    role=role,
                    unit=SCHOOL_NAME,
                    grade=(grade or None) if (role == "teacher" and is_hm_flag) else None,
                    class_no=(class_no or None) if (role == "teacher" and is_hm_flag) else None,
                    is_homeroom=is_hm_flag if role == "teacher" else 0,
                )
                db.session.add(u)
                db.session.commit()

                
                if role == "teacher" and ta_subjects and ta_grades and ta_classes:
                    now = datetime.utcnow()
                    for s, g1, c1 in zip(ta_subjects, ta_grades, ta_classes):
                        s = (s or "").strip()
                        g1 = (g1 or "").strip()
                        c1 = (c1 or "").strip()
                        if not (s and g1 and c1):
                            continue
                        if s not in CATEGORY_OPTIONS:
                            continue
                        try:
                            db.session.execute(
                                text(
                                    """
                                INSERT INTO teaching_assignment
                                    (teacher_username, subject, grade, class_no, created_at)
                                VALUES
                                    (:t, :s, :g, :c, :dt)
                                """
                                ),
                                {"t": username, "s": s, "g": g1, "c": c1, "dt": now},
                            )
                        except Exception:
                            db.session.rollback()
                    db.session.commit()

                login_user(User.query.filter_by(username=username).first())
                return redirect(url_for(role_endpoint(role)))
        else:
            sid = parent_code_to_sid(parent_code)
            stu = User.query.filter_by(username=sid, role="student").first() if sid else None
            if not stu:
                msg = "邀請碼無效。"
            else:
                db.session.add(
                    User(
                        username=username,
                        display_name=display,
                        password=pw,
                        role="parent",
                        unit=SCHOOL_NAME,
                        grade=stu.grade,
                        class_no=stu.class_no,
                    )
                )
                db.session.commit()
                if not ParentChild.query.filter_by(parent_name=username, student_name=stu.username).first():
                    db.session.add(ParentChild(parent_name=username, student_name=stu.username))
                    db.session.commit()
                login_user(User.query.filter_by(username=username).first())
                return redirect(url_for("parent"))

    grade_opts   = opt(GRADE_OPTIONS)
    class_opts   = opt(CLASS_OPTIONS)
    subject_opts = "".join(f"<option value='{s}'>{s}</option>" for s in CATEGORY_OPTIONS)

    form = (
        "<div class='auth-simple auth-simple--wide'>"
        "<section class='auth-card register-card'>"
        "<div class='auth-card__head'>"
        "<div><div class='auth-eyebrow'>註冊</div><h2>建立新帳號</h2></div>"
        "<a class='btn btn-sm btn-outline-secondary' href='/login'>返回登入</a>"
        "</div>"
        "<form method='post' class='register-form'>"
        "<section class='register-section'>"
        "<div class='register-section__title'>基本資料</div>"
        "<div class='register-grid'>"
        "<div><label class='form-label'>顯示名稱</label>"
        "<input name='display_name' class='form-control form-control-lg' placeholder='請輸入姓名或稱呼' required></div>"
        "<div><label class='form-label'>帳號</label>"
        "<input name='username' class='form-control form-control-lg' placeholder='限英文字母與數字' required pattern='[A-Za-z0-9]+'></div>"
        "<div><label class='form-label'>身分</label>"
        "<select name='role' id='role' class='form-select form-select-lg' required>"
        "<option value=''>請選擇身分</option>"
        "<option value='leader'>組長</option>"
        "<option value='teacher'>老師</option>"
        "<option value='parent'>家長</option>"
        "</select></div>"
        "</div>"
        "</section>"

        "<section class='register-section staff-block' style='display:none;'>"
        "<div class='register-section__title'>校內人員資料</div>"
        "<div><label class='form-label'>邀請碼</label>"
        "<input type='password' name='invite' class='form-control form-control-lg' inputmode='numeric' pattern='\\d{10}' autocomplete='off' placeholder='請輸入 10 位邀請碼'></div>"

        "<div class='teacher-only mt-3'>"
        "<label class='setting-check' for='is_homeroom'>"
        "<input class='form-check-input setting-check__input' type='checkbox' id='is_homeroom' name='is_homeroom'>"
        "<span class='setting-check__copy'>"
        "<span class='setting-check__title'>班導師</span>"
        "<span class='setting-check__hint'>若您是班導，請選擇負責班級。</span>"
        "</span>"
        "</label>"
        "<div class='register-grid homeroom-row mt-3' style='display:none;'>"
        f"<div><label class='form-label'>年級</label>"
        f"<select id='grade' name='grade' class='form-select form-select-lg'><option value=''>請選擇年級</option>{grade_opts}</select></div>"
        f"<div><label class='form-label'>班級</label>"
        f"<select id='class_no' name='class_no' class='form-select form-select-lg'><option value=''>請選擇班級</option>{class_opts}</select></div>"
        "</div>"
        "<div class='register-subcard mt-3'>"
        "<div class='register-subcard__head'>"
        "<strong>授課班級</strong>"
        "<button class='btn btn-sm btn-outline-secondary' type='button' id='addRow'>新增一列</button>"
        "</div>"
        "<div id='taRows' class='register-repeat-list'></div>"
        "<div class='form-text mt-2'>可先略過，之後再由組長或管理員調整。</div>"
        "</div>"
        "</div>"
        "</section>"

        "<section class='register-section parent-block' style='display:none;'>"
        "<div class='register-section__title'>家長綁定</div>"
        "<div><label class='form-label'>家長邀請碼</label>"
        "<input type='password' name='parent_code' class='form-control form-control-lg' inputmode='numeric' autocomplete='off' placeholder='請輸入孩子提供的邀請碼'>"
        "<div class='form-text'>完成綁定後即可查看孩子的聯絡簿與簽閱內容。</div></div>"
        "</section>"

        "<section class='register-section'>"
        "<div class='register-section__title'>密碼設定</div>"
        "<div class='register-grid'>"
        "<div><label class='form-label'>密碼</label>"
        "<input type='password' name='password' class='form-control form-control-lg' placeholder='請輸入密碼' required></div>"
        "<div><label class='form-label'>確認密碼</label>"
        "<input type='password' name='password2' class='form-control form-control-lg' placeholder='再次輸入密碼' required></div>"
        "</div>"
        "</section>"
        "<button class='btn btn-primary btn-lg w-100'>建立帳號</button>"
        "</form>"
        "</section>"
        "</div>"

        f"<template id='tplRow'>"
        f"<div class='ta-row register-repeat-row'>"
        f"<div><label class='form-label'>科目</label>"
        f"<select name='ta_subject[]' class='form-select' required>"
        f"<option value=''>請選擇</option>{subject_opts}</select></div>"
        f"<div><label class='form-label'>年級</label>"
        f"<select name='ta_grade[]' class='form-select' required>"
        f"<option value=''>請選擇</option>{grade_opts}</select></div>"
        f"<div><label class='form-label'>班級</label>"
        f"<select name='ta_class[]' class='form-select' required>"
        f"<option value=''>請選擇</option>{class_opts}</select></div>"
        f"<button type='button' class='btn btn-sm btn-outline-danger removeRow'>刪除</button>"
        f"</div></template>"

        
        "<script>"
        "const r=document.getElementById('role');"
        "const staff=document.querySelector('.staff-block');"
        "const parentBlk=document.querySelector('.parent-block');"
        "const teacherOnly=document.querySelector('.teacher-only');"
        "const isHm=document.getElementById('is_homeroom');"
        "const hmRow=document.querySelector('.homeroom-row');"
        "const gradeEl=document.getElementById('grade');"
        "const classEl=document.getElementById('class_no');"

        "const add=document.getElementById('addRow');"
        "const container=document.getElementById('taRows');"
        "const tpl=document.getElementById('tplRow');"

        "function syncRole(){"
        "  const v=r.value;"
        "  staff.style.display=(v==='teacher'||v==='leader')?'block':'none';"
        "  parentBlk.style.display=(v==='parent')?'block':'none';"
        "  teacherOnly.style.display=(v==='teacher')?'block':'none';"
        "  if(v!=='teacher'){ hmRow.style.display='none'; gradeEl.required=false; classEl.required=false; }"
        "}"
        "function syncHomeroom(){"
        "  const on=isHm.checked;"
        "  hmRow.style.display=on?'grid':'none';"
        "  gradeEl.required=on; classEl.required=on;"
        "}"
        "r.addEventListener('change',syncRole);"
        "isHm.addEventListener('change',syncHomeroom);"
        "document.addEventListener('DOMContentLoaded',()=>{syncRole();syncHomeroom();});"

        "add?.addEventListener('click',()=>{container.appendChild(tpl.content.cloneNode(true));});"
        "container?.addEventListener('click',(e)=>{"
        "  if(e.target.classList.contains('removeRow')){"
        "    e.target.closest('.ta-row')?.remove();"
        "  }"
        "});"
        "</script>"
    )
    return page("註冊新帳號", form, msg=msg)

@app.route("/user/<username>")
@login_required
def view_user(username):
    u = User.query.filter_by(username=username).first_or_404()

    
    avatar = getattr(u, "avatar", None)
    avatar_html = (f"<img src='/uploads/{avatar}' style='width:120px;height:120px;object-fit:cover;border-radius:50%;border:1px solid #ddd;'>"
                   if avatar else "<div style='width:120px;height:120px;border-radius:50%;background:#eee;display:flex;align-items:center;justify-content:center;color:#888;'>No Photo</div>")

    
    cls_txt = ""
    if (u.grade and u.class_no):
        cls_txt = f"{u.grade}年{u.class_no}班"
        if str(u.is_homeroom or 0) == "1" and u.role == "teacher":
            cls_txt += "（班導）"

    
    show_contact = can_view_contact(current_user, u)

    
    def safe(v): return (v or "").strip()

    
    header = (
        "<div class='d-flex gap-3 align-items-center mb-3'>"
        f"{avatar_html}"
        "<div>"
        f"<div class='h5 mb-1'>{safe(u.display_name) or u.username}</div>"
        f"<div class='text-muted'>{SCHOOL_NAME}"
        f"{(' · ' + cls_txt) if cls_txt else ''}"
        f" · 身份：{u.role}</div>"
        f"{('<div class=\"text-muted small\">' + safe(getattr(u,'title','')) + '</div>') if safe(getattr(u,'title','')) else ''}"
        "</div></div>"
    )

    
    bio_html = f"<div class='card p-3'><div class='fw-bold mb-2'>個人簡介</div><div>{(safe(getattr(u,'bio','')) or '（未填寫）')}</div></div>"

    
    if show_contact:
        contact_rows = []
        if safe(getattr(u,'email','')): contact_rows.append(f"<div>Email：{safe(getattr(u,'email',''))}</div>")
        if safe(getattr(u,'phone','')): contact_rows.append(f"<div>電話：{safe(getattr(u,'phone',''))}</div>")
        if safe(getattr(u,'line_id','')): contact_rows.append(f"<div>LINE 帳號：{safe(getattr(u,'line_id',''))}</div>")
        if safe(getattr(u,'office_hours','')): contact_rows.append(f"<div>可聯絡時段：{safe(getattr(u,'office_hours',''))}</div>")
        contact_body = "".join(contact_rows) or "（未提供聯絡方式）"
    else:
        contact_body = "<div class='text-muted'>此使用者未對你公開聯絡方式。</div>"

    contact_html = f"<div class='card p-3'><div class='fw-bold mb-2'>聯絡資訊</div><div>{contact_body}</div></div>"

    
    subj = safe(getattr(u,'subjects',''))
    subj_html = f"<div class='card p-3'><div class='fw-bold mb-2'>教授科目</div><div>{(subj or '（未填寫）')}</div></div>"

    
    edit_btn = ""
    if current_user.username == u.username:
        edit_btn = "<div class='mt-3'><a class='btn btn-sm btn-outline-primary' href='/profile'>編輯我的檔案</a></div>"

    content = (
        header +
        "<div class='row g-3'>"
        "<div class='col-md-8'>" + bio_html + "<div class='mt-3'>" + subj_html + "</div></div>"
        "<div class='col-md-4'>" + contact_html + edit_btn + "</div>"
        "</div>"
    )
    return page("個人檔案", content)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))


@app.route("/admin")
@roles_required("admin")
def admin():
    abort(404)
    shortcuts = [
        ("使用者管理", "帳號、角色、密碼調整", url_for("admin_users")),
        ("作業 / 任務總管", "檢視與調整所有作業、永續任務", url_for("admin_tasks")),
        ("日記題目", "集中管理各班日記題目與繳交情況", url_for("teacher_diary")),
        ("公告管理", "發布/調整全校或各班公告", url_for("teacher_announce")),
        ("親師交流", "快速查看與回覆老師、家長訊息", url_for("comm")),
        ("用藥紀錄", "查看各班家長送出的用藥需求", url_for("medication_records")),
        ("共讀 / 閱讀審核", "審核閱讀認證與親子共讀資料", url_for("reading_review_queue")),
        ("課表 / 圖片", "上傳或移除課表、活動等圖片", url_for("timetable_images")),
        ("上傳照片管理", "批次檢查並刪除不再需要的舊圖片", url_for("admin_photos")),
        ("閱讀任務儀表板", "設定閱讀目標並檢視學生進度", url_for("reading_teacher_dashboard")),
    ]

    cards = "".join(
        f"""
        <div class="col-sm-6 col-lg-3">
          <div class="card border-0 shadow-sm h-100">
            <div class="card-body">
              <div class="h6 mb-2">{title}</div>
            </div>
            <a class="stretched-link" href="{href}"></a>
          </div>
        </div>
        """
        for title, _desc, href in shortcuts
    )

    photo_preview = _collect_upload_photos()
    preview_cards = ""
    if photo_preview:
        limited = photo_preview[:8]
        preview_cards = "".join(
            f"""
            <div class="col">
              <div class="card h-100">
                <div class="card-body text-center">
                  <div class="mb-2"><a href="{p['url']}" target="_blank" rel="noopener">
                    <img src="{p['url']}" style="max-width:120px;max-height:120px;border-radius:8px;"></a></div>
                  <div class="small text-muted" style="word-break:break-all;">{os.path.basename(p['rel'])}</div>
                  <div class="small text-muted">{p['mtime']}</div>
                  <div class="form-check mt-1">
                    <input class="form-check-input" type="checkbox" name="photos" value="{p['rel']}" id="adm_photo_{idx}">
                    <label class="form-check-label small" for="adm_photo_{idx}">刪除</label>
                  </div>
                </div>
              </div>
            </div>
            """
            for idx, p in enumerate(limited)
        )
        photo_block = f"""
        <div class='card border-0 shadow-sm mt-4'>
          <div class='card-body'>
            <div class='d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3'>
              <div>
                <h5 class='mb-1'>上傳照片管理</h5>
                <div class='text-muted small'>勾選後刪除。</div>
              </div>
              <a class='btn btn-sm btn-outline-secondary' href='{url_for("admin_photos")}'>開啟完整管理頁</a>
            </div>
            <form method='post' action='{url_for("admin_photos")}'>
              <div class='row row-cols-2 row-cols-md-4 g-3'>
                {preview_cards}
              </div>
              <div class='mt-3 d-flex justify-content-between align-items-center'>
                <div class='small text-muted'>僅顯示前 {len(limited)} 張，共 {len(photo_preview)} 張。</div>
                <button class='btn btn-danger btn-sm'
                        onclick="return confirm('確定要刪除勾選的照片嗎？此動作無法復原。');">
                  刪除勾選照片
                </button>
              </div>
            </form>
          </div>
        </div>
        """
    else:
        photo_block = (
            "<div class='card border-0 shadow-sm mt-4'><div class='card-body'>"
            "<h5 class='mb-1'>上傳照片管理</h5>"
            "<div class='text-muted small'>目前沒有可顯示的圖片。</div>"
            "</div></div>"
        )

    content = f"<div class='row g-3'>{cards}</div>" + photo_block
    return page("管理員後台", content)

@app.route("/admin_users")
@roles_required("admin")
def admin_users():
    abort(404)
    q=(request.args.get("q") or "").strip()
    role=(request.args.get("role") or "").strip()
    query=User.query
    if q:
        like=f"%{q}%"; query=query.filter((User.username.ilike(like)) | (User.display_name.ilike(like)))
    if role: query=query.filter(User.role==role)
    users=query.order_by(User.role.desc(), User.username.asc()).all()

    def row(u):
        scope=" · ".join([x for x in [u.unit, (u.grade and f'{u.grade}年'), (u.class_no and f'{u.class_no}班')] if x])
        del_btn="" if u.username==current_user.username else f"<a class='btn btn-sm btn-outline-danger' href='/admin_delete/{u.username}' onclick='return confirm(\"刪除 {u.username}？\");'>刪除</a>"
        edit=f"<a class='btn btn-sm btn-outline-primary me-2' href='/admin_user_edit/{u.username}'>改名/改密碼</a>"
        return f"<tr><td>{u.username}</td><td>{u.display_name or ''}</td><td>{u.role}</td><td>{scope}</td><td class='text-end'>{edit}{del_btn}</td></tr>"

    body = "".join(row(u) for u in users) or "<tr><td colspan='5' class='text-center'>無</td></tr>"
    return page("管理員 - 使用者管理",
        "<form method='get' class='row g-2 mb-3'>"
        f"<div class='col-md-6'><input class='form-control' name='q' placeholder='帳號/名稱' value='{q}'></div>"
        "<div class='col-md-3'><select name='role' class='form-select'>"
        f"<option value=''>（全部角色）</option>"
        + "".join(f"<option value='{r}' {'selected' if role==r else ''}>{r}</option>" for r in ["admin","leader","teacher","parent","student"])
        + "</select></div>"
        "<div class='col-md-3 d-grid'><button class='btn btn-primary'>篩選</button></div></form>"
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th>帳號</th><th>顯示名稱</th><th>角色</th><th>所屬</th><th class='text-end'>操作</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )

@app.route("/admin_user_edit/<username>", methods=["GET","POST"])
@roles_required("admin")
def admin_user_edit(username):
    abort(404)
    u = User.query.filter_by(username=username).first_or_404()
    if request.method=="POST":
        if g("display_name"): u.display_name=g("display_name")
        if g("password"): u.password=g("password")
        db.session.commit()
        return toast_redirect("admin_users", "使用者已更新", "success")
    scope = " · ".join([x for x in [u.unit, (u.grade and f'{u.grade}年'), (u.class_no and f'{u.class_no}班')] if x])
    form = (
        "<form method='post' style='max-width:520px;'>"
        f"<div class='mb-2'><label class='form-label'>帳號（不可改）</label><input class='form-control' value='{u.username}' disabled></div>"
        f"<div class='mb-2'><label class='form-label'>所屬</label><input class='form-control' value='{scope}' disabled></div>"
        f"<div class='mb-3'><label class='form-label'>顯示名稱</label><input name='display_name' class='form-control' value='{u.display_name or u.username}'></div>"
        "<div class='mb-3'><label class='form-label'>新密碼（留空則不變更）</label><input name='password' type='text' class='form-control' placeholder='不修改則留空'></div>"
        "<button class='btn btn-success'>儲存</button> <a class='btn btn-outline-secondary' href='/admin_users'>返回</a></form>"
    )
    return page("管理員 - 修改使用者", form)

@app.route("/admin_delete/<username>")
@roles_required("admin")
def admin_delete_user(username):
    abort(404)
    if username == current_user.username:
        return toast_redirect("admin_users", "不可刪除自己。", "warning")

    user = User.query.filter_by(username=username).first_or_404()

    
    ParentChild.query.filter(
        (ParentChild.parent_name == username) | (ParentChild.student_name == username)
    ).delete()

    CompletedTask.query.filter_by(student_name=username).delete()

    
    for t in Task.query.filter_by(created_by=username).all():
        CompletedTask.query.filter_by(task_id=t.id).delete()
        db.session.delete(t)

    
    db.session.delete(user)
    db.session.commit()

    return toast_redirect("admin_users", "使用者與相關資料已刪除。", "success")


def task_form_block(unit_opts, grade_opts, class_opts, scope_id):
    return (
        "<div class='row g-2 mt-1'>"
        f"<div class='col-md-2'><label class='form-label'>範圍</label><select name='scope' id='{scope_id}' class='form-select'>"
        "<option value='school'>全校</option><option value='grade'>年級</option><option value='class'>班級</option></select></div>"
        f"<div class='col-md-2 scope-grade' style='display:none;'><label class='form-label'>年級</label><select name='grade' class='form-select'><option value=''>請選擇</option>{grade_opts}</select></div>"
        f"<div class='col-md-2 scope-class' style='display:none;'><label class='form-label'>班級</label><select name='class_no' class='form-select'><option value=''>請選擇</option>{class_opts}</select></div>"
        "<div class='col-md-3'><label class='form-label'>截止日</label><input type='date' name='end_date' class='form-control'></div>"
        "<div class='col-md-5'><label class='form-label'>描述</label><input name='description' class='form-control' required></div></div>"
        f"<script>const sc_{scope_id}=document.getElementById('{scope_id}');function vis_{scope_id}(){{const v=sc_{scope_id}.value;const box=sc_{scope_id}.closest('form')||document;const g=box.querySelector('.scope-grade');const c=box.querySelector('.scope-class');if(g)g.style.display=(v==='grade'||v==='class')?'block':'none';if(c)c.style.display=(v==='class')?'block':'none';}}sc_{scope_id}.addEventListener('change',vis_{scope_id});document.addEventListener('DOMContentLoaded',vis_{scope_id});</script>"
    )

@app.route("/admin_tasks", methods=["GET","POST"])
@roles_required("admin")
def admin_tasks():
    abort(404)
    msg=""
    if request.method=="POST":
        title=g("title"); desc=g("description")
        image_file = request.files.get("image")
        saved_img = save_image(image_file) if (image_file and getattr(image_file, "filename", "").strip()) else None
        task_type = g("task_type") or "homework"
        cat_hw    = g("category") or "其他"
        cat_ms    = g("mission_category") or "其他"
        cat_final, ms_final = (cat_hw, None) if task_type=="homework" else (None, cat_ms)
        pts=g("points") or "0"
        points=int(pts) if pts.isdigit() else 0
        scope=g("scope","school")
        gsel, csel = g("grade"), g("class_no")
        end=parse_date(g("end_date"))

        if title and desc:
            t=Task(title=title, description=desc, created_by=current_user.username,
                   category=cat_final, mission_category=ms_final,
                   points=max(points,0),
                   start_date=today(), end_date=end, unit=SCHOOL_NAME,
                   task_type=task_type,
                   is_view_only=1 if task_type=="homework" else 0)
            _set_task_image_if_possible(t, saved_img)
            if scope=="school": t.is_school_wide=1
            elif scope=="grade" and gsel in GRADE_OPTIONS: t.grade=gsel
            elif scope=="class" and (gsel in GRADE_OPTIONS and csel in CLASS_OPTIONS): t.grade, t.class_no = gsel, csel
            else: msg="範圍設定不正確。"
            if not msg:
                db.session.add(t); db.session.commit()
                return toast_redirect("admin_tasks", "已建立項目。", "success")

    q=(request.args.get("q") or "").strip()
    query=Task.query.filter(Task.unit==SCHOOL_NAME)
    if q:
        like=f"%{q}%"; query=query.filter((Task.title.ilike(like)) | (Task.description.ilike(like)))
    tasks=query.order_by(Task.id.desc()).all()

    def trow(t):
        scope = scope_txt(t)
        type_tag = mission_badge(t)
        thumb = img_html(task_img_name(t), maxw=180)
        return ("<tr><td>{}</td><td>{}{}{}</td><td>{}</td><td>{}</td><td>{}</td>"
                "<td><a class='btn btn-sm btn-warning me-2' href='/edit_task/{}'>改</a>"
                "<a class='btn btn-sm btn-danger' href='/admin_task_delete/{}' onclick='return confirm(\"刪除？\");'>刪</a></td></tr>"
                ).format(t.id, t.title, type_tag, thumb, scope, cat_label(t), (t.end_date or ""), t.id, t.id)
    body="".join(map(trow,tasks)) or "<tr><td colspan='6' class='text-center'>無資料</td></tr>"

    hw_opts = "".join(f"<option value='{c}'>{c}</option>" for c in CATEGORY_OPTIONS)
    ms_opts = "".join(f"<option value='{c}'>{c}</option>" for c in MISSION_CATEGORY_OPTIONS)
    grade_opts=opt(GRADE_OPTIONS); class_opts=opt(CLASS_OPTIONS)

    form = (
        "<form method='post' enctype='multipart/form-data' class='mb-3 p-3 border rounded'>"
        "<div class='row g-2'>"
        "<div class='col-md-3'><label class='form-label'>標題</label><input name='title' class='form-control' required></div>"
        "<div class='col-md-3'><label class='form-label'>類型</label><select name='task_type' id='task_type' class='form-select'>"
        "<option value='homework' selected>作業（僅閱讀）</option>"
        "<option value='mission'>永續閱讀任務（可繳交）</option>"
        "</select></div>"
        "<div class='col-md-3' id='hw_cat'><label class='form-label'>科目分類</label><select name='category' class='form-select'>"
        + hw_opts + "</select></div>"
        "<div class='col-md-3' id='ms_cat' style='display:none;'><label class='form-label'>永續分類</label><select name='mission_category' class='form-select'>"
        + ms_opts + "</select></div>"
        "</div>"
        "<div class='row g-2 mt-1'>"
        "<div class='col-md-2'><label class='form-label'>積分</label><input type='number' name='points' class='form-control' value='0' min='0' step='1'></div>"
        "</div>"
        + task_form_block("", grade_opts, class_opts, "scope")
        + "<div class='row g-2 mt-1'>"
          "<div class='col-md-4'><label class='form-label'>截止日</label><input type='date' name='end_date' class='form-control'></div>"
          "<div class='col-md-4'><label class='form-label'>描述</label><input name='description' class='form-control' required></div>"
          "<div class='col-md-4'><label class='form-label'>附圖（可留空）</label><input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"
          "</div>"
        "<div class='form-text'>發布日期為今天，適用學校：<b>"+SCHOOL_NAME+"</b></div>"
        "<button class='btn btn-primary mt-2'>建立</button>"
        "<script>const tp=document.getElementById('task_type');const hw=document.getElementById('hw_cat');const ms=document.getElementById('ms_cat');"
        "function vis(){const v=tp.value;hw.style.display=(v==='homework')?'block':'none';ms.style.display=(v==='mission')?'block':'none';}"
        "tp.addEventListener('change',vis);document.addEventListener('DOMContentLoaded',vis);</script>"
        "</form>"
    )
    filt = (
        "<form method='get' class='row g-2 mb-3'>"
        f"<div class='col-md-10'><input class='form-control' name='q' placeholder='關鍵字' value='{q}'></div>"
        "<div class='col-md-2 d-grid'><button class='btn btn-outline-primary'>篩選</button></div></form>"
    )
    table = "<div class='table-responsive'><table class='table table-sm align-middle'><thead><tr><th>編號</th><th>標題/類型</th><th>範圍</th><th>分類</th><th>截止</th><th>操作</th></tr></thead><tbody>{}</tbody></table></div>".format(body)
    return page("管理員 - 項目總管", form + filt + table, msg=msg)

def is_staff_admin():
    return bool(current_user and current_user.role in ("leader","admin"))

def is_staff_admin():
    return bool(current_user and current_user.role in ("leader", "admin"))


def _date_in_range(d: date_cls | None, s: date_cls | None, e: date_cls | None) -> bool:
    return (d is not None and s is not None and e is not None and s <= d <= e)

def _weekday_0_is_mon(d: date_cls) -> int:
    
    return int(d.weekday())

def current_semester(unit: str, d: date_cls | None = None) -> Semester | None:
    d = (d or today())
    return (Semester.query
            .filter_by(unit=unit)
            .filter(Semester.start_date <= d, Semester.end_date >= d)
            .order_by(Semester.start_date.desc())
            .first())

def _week_spans(sem: Semester) -> list[tuple[int, date_cls, date_cls]]:
    """依學期起迄產生週區間（週一~週日）。"""
    spans = []
    
    cur = sem.start_date - timedelta(days=sem.start_date.weekday())
    idx = 1
    while cur <= sem.end_date:
        s = cur
        e = min(cur + timedelta(days=6), sem.end_date)
        spans.append((idx, s, e))
        cur = e + timedelta(days=1)
        idx += 1
    return spans

def semester_of_date(unit: str, d: date_cls | None) -> tuple[Semester | None, int | None]:
    """回傳 (學期物件, 第幾週)；週次優先查 semester_week，沒有就動態推算。"""
    d = d or today()
    sem = current_semester(unit, d)
    if not sem:
        return None, None
    wk = (SemesterWeek.query
          .filter_by(semester_id=sem.id)
          .filter(SemesterWeek.start_date <= d, SemesterWeek.end_date >= d)
          .first())
    if wk:
        return sem, int(wk.week_no)
    
    start_monday = sem.start_date - timedelta(days=sem.start_date.weekday())
    delta = (d - start_monday).days
    week_no = delta // 7 + 1
    return sem, max(1, week_no)


def _week_spans(sem: Semester) -> list[tuple[int, datetime.date, datetime.date]]:
    """依學期起迄產生週區間（週一~週日）。"""
    from datetime import timedelta
    spans = []
    
    cur = sem.start_date - timedelta(days=sem.start_date.weekday())
    idx = 1
    while cur <= sem.end_date:
        s = cur
        e = min(cur + timedelta(days=6), sem.end_date)
        spans.append((idx, s, e))
        cur = e + timedelta(days=1)
        idx += 1
    return spans

@app.route("/assign_teachers", methods=["GET", "POST"])
@login_required
def assign_teachers():
    if not is_staff_admin():
        return redirect(url_for("home"))

    msg = ""
    
    teachers = User.query.filter_by(role="teacher").order_by(User.username.asc()).all()
    t_opts = "".join(
        f"<option value='{t.username}'>{t.display_name or t.username}</option>"
        for t in teachers
    )

    selected = request.values.get("teacher") or (teachers[0].username if teachers else "")

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "add" and selected:
            s_list = request.form.getlist("ta_subject[]")
            g_list = request.form.getlist("ta_grade[]")
            c_list = request.form.getlist("ta_class[]")
            now = datetime.utcnow()
            ok, dup, err = 0, 0, 0

            for s, g, c in zip(s_list, g_list, c_list):
                s = (s or "").strip()
                g = (g or "").strip()
                c = (c or "").strip()
                if not (s and g and c):
                    continue
                if s not in CATEGORY_OPTIONS:
                    continue
                try:
                    db.session.execute(
                        text(
                            """
                            INSERT INTO teaching_assignment (teacher_username, subject, grade, class_no, created_at)
                            VALUES (:t, :s, :g, :c, :dt)
                            """
                        ),
                        {"t": selected, "s": s, "g": g, "c": c, "dt": now},
                    )
                    ok += 1
                except Exception as e:
                    db.session.rollback()
                    if "uq_teacher_subject_class" in str(e):
                        dup += 1
                    else:
                        err += 1
            db.session.commit()
            msg = f"新增完成：{ok} 筆，重複 {dup} 筆，失敗 {err} 筆。"

        elif action == "del":
            
            tid = request.form.get("tid")
            try:
                db.session.execute(
                    text("DELETE FROM teaching_assignment WHERE id=:id"), {"id": tid}
                )
                db.session.commit()
                msg = "已刪除。"
            except Exception:
                db.session.rollback()
                msg = "刪除失敗。"

    
    assigns = []
    if selected:
        assigns = db.session.execute(
            text(
                """
                SELECT id, subject, grade, class_no
                FROM teaching_assignment
                WHERE teacher_username=:t
                ORDER BY grade, class_no, subject
                """
            ),
            {"t": selected},
        ).mappings().all()

    subject_opts = "".join(f"<option value='{s}'>{s}</option>" for s in CATEGORY_OPTIONS)
    grade_opts = opt(GRADE_OPTIONS)
    class_opts = opt(CLASS_OPTIONS)

    content = f"""
    <form method="get" class="mb-3">
      <div class="row g-2 align-items-end">
        <div class="col-md-6">
          <label class="form-label">選擇老師</label>
          <select name="teacher" class="form-select" onchange="this.form.submit()">
            {t_opts}
          </select>
        </div>
      </div>
    </form>

    <div class="row g-4">
      <div class="col-md-6">
        <div class="card p-3">
          <h6 class="mb-2">新增指派給：<span class="text-primary">{selected}</span></h6>
          <form method="post" id="addForm">
            <input type="hidden" name="teacher" value="{selected}">
            <input type="hidden" name="action" value="add">
            <div id="rows"></div>
            <div class="d-flex gap-2 mt-2">
              <button type="button" class="btn btn-outline-secondary" id="addRow">＋新增一列</button>
              <button class="btn btn-success">儲存</button>
            </div>
          </form>
          {("<div class='alert alert-warning mt-3'>" + msg + "</div>") if msg else ""}
        </div>
      </div>

      <div class="col-md-6">
        <div class="card p-3">
          <h6 class="mb-2">已指派清單</h6>
          {"<ul class='list-group'>" + "".join(
            f"<li class='list-group-item d-flex justify-content-between align-items-center'>"
            f"<div><b>{a['grade']}年{a['class_no']}班</b>／{a['subject']}</div>"
            f"<form method='post' class='m-0'>"
            f"<input type='hidden' name='teacher' value='{selected}'>"
            f"<input type='hidden' name='action' value='del'>"
            f"<input type='hidden' name='tid' value='{a['id']}'>"
            f"<button class='btn btn-sm btn-outline-danger'>刪除</button>"
            f"</form></li>"
            for a in assigns
          ) + "</ul>" if assigns else "<div class='alert alert-info'>尚無指派。</div>"}
        </div>
      </div>
    </div>

    <template id="tpl">
      <div class="row g-2 align-items-end ta-row">
        <div class="col-md-6">
          <label class="form-label">科目</label>
          <select name="ta_subject[]" class="form-select" required>
            <option value="">請選擇</option>{subject_opts}
          </select>
        </div>
        <div class="col-md-3">
          <label class="form-label">年級</label>
          <select name="ta_grade[]" class="form-select" required>
            <option value="">請選擇</option>{grade_opts}
          </select>
        </div>
        <div class="col-md-3">
          <label class="form-label">班級</label>
          <select name="ta_class[]" class="form-select" required>
            <option value="">請選擇</option>{class_opts}
          </select>
        </div>
        <div class="col-12 text-end">
          <button type="button" class="btn btn-sm btn-outline-danger removeRow">刪除這列</button>
        </div>
      </div>
    </template>

    <script>
      const add = document.getElementById('addRow');
      const rows = document.getElementById('rows');
      const tpl = document.getElementById('tpl');
      add?.addEventListener('click', () => {{
        rows.appendChild(tpl.content.cloneNode(true));
      }});
      rows?.addEventListener('click', (e) => {{
        if (e.target.classList.contains('removeRow')) {{
          e.target.closest('.ta-row')?.remove();
        }}
      }});
    </script>
    """
    return page("管理員：科任指派分配", content)

@app.route("/teacher_diary", methods=["GET", "POST"])
@roles_required("teacher", "admin")
def teacher_diary():
    """
    老師：發布本班「日記題目」，並查看繳交/公開數；可進入「查看/回饋」頁面
    （同班同日唯一：若同一天已有題目則改為更新）
    """
    unit, grade, class_no = current_user.unit, current_user.grade, current_user.class_no
    msg = ""

    if request.method == "POST":
        title = g("title")
        d = parse_date(g("date")) or local_today()
        instruction = g("instruction")
        image_file = request.files.get("image")
        saved_img = save_image(image_file) if (image_file and getattr(image_file, "filename", "").strip()) else None
        if not title:
            msg = "請輸入題目。"
        else:
            
            exist = (DiaryPrompt.query
                     .filter_by(unit=unit, grade=grade, class_no=class_no, date=d)
                     .first())
            if exist:
                exist.title = title
                exist.instruction = instruction
                if saved_img:
                    exist.image = saved_img
                exist.created_by = current_user.username
                exist.created_at = datetime.utcnow()
                db.session.commit()
                return toast_redirect("teacher_diary", "已更新今日日記題目。", "success")
            else:
                db.session.add(DiaryPrompt(
                    date=d, title=title, instruction=instruction,
                    unit=unit, grade=grade, class_no=class_no,
                    created_by=current_user.username,
                    image=saved_img
                ))
                db.session.commit()
                return toast_redirect("teacher_diary", "已發布日記題目。", "success")

    
    prompts = (DiaryPrompt.query
               .filter_by(unit=unit, grade=grade, class_no=class_no)
               .order_by(DiaryPrompt.date.desc(), DiaryPrompt.id.desc())
               .all())

    
    def counts(pid: int):
        total = db.session.query(func.count(DiarySubmission.id)).filter_by(prompt_id=pid).scalar() or 0
        pub = db.session.query(func.count(DiarySubmission.id)).filter_by(prompt_id=pid, share_to_parent=1).scalar() or 0
        return int(total), int(pub)

    
    def row(p: "DiaryPrompt"):
        t, pub = counts(p.id)
        thumb = img_html(diary_prompt_img_name(p), maxw=220)
        return (
            f"<tr>"
            f"<td class='text-nowrap'>{p.date}</td>"
            f"<td><div class='fw-bold'>{p.title}</div>"
            f"{('<div class=\"small text-muted\">' + _user_text_html(p.instruction) + '</div>') if p.instruction else ''}"
            f"{thumb}</td>"
            f"<td class='text-center'>{t}</td>"
            f"<td class='text-center'>{pub}</td>"
            f"<td class='text-end'>"
            f"<a class='btn btn-sm btn-outline-primary me-2' href='/teacher_diary_detail/{p.id}'>查看/回饋</a>"
            f"<a class='btn btn-sm btn-outline-danger' href='/teacher_diary_delete/{p.id}' onclick='return confirm(\"確定刪除？（會一併刪此題目的所有日記）\");'>刪除</a>"
            f"</td></tr>"
        )

    body = "".join(row(p) for p in prompts) or "<tr><td colspan='5' class='text-center'>尚無題目</td></tr>"

    
    form = (
        "<form method='post' class='mb-4' enctype='multipart/form-data'>"
        "<div class='row g-2'>"
        f"<div class='col-md-3'><label class='form-label'>日期</label><input type='date' name='date' value='{local_today()}' class='form-control' required></div>"
        "<div class='col-md-9'><label class='form-label'>題目（必填）</label><input name='title' class='form-control' placeholder='例：今天最開心的事是什麼？' required></div>"
        "</div>"
        "<div class='mt-2'><label class='form-label'>說明（可留空）</label>"
        "<textarea name='instruction' class='form-control' rows='2' placeholder='寫 100–200 字，盡量具體描述喔！'></textarea></div>"
        "<div class='mt-2'><label class='form-label'>題目附圖（可留空）</label>"
        "<input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"
        "<button class='btn btn-primary mt-2'>發布題目</button></form>"
    )

    table = (
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th>日期</th><th>題目 / 說明</th><th class='text-center'>繳交</th><th class='text-center'>公開</th><th class='text-end'>操作</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )
    head = f"<p>班級：{unit or ''} · {grade}年{class_no}班</p>"
    return page("老師｜班級日記（發布題目）", head + form + table, msg=msg)

@app.route("/teacher_day")
@roles_required("teacher")
def teacher_day():
    unit, grade, class_no = current_user.unit, current_user.grade, current_user.class_no
    dsel = parse_date((request.args.get("date") or "")) or today()

    
    students = (
        User.query
        .filter_by(role="student", unit=unit, grade=grade, class_no=class_no)
        .order_by(User.username.asc())
        .all()
    )

    
    base = (
        Task.query.filter(Task.unit == unit)
        .filter(
            (Task.is_school_wide == 1)
            | ((Task.grade == grade) & (Task.class_no == class_no))
        )
    )
    day_tasks = base.filter(Task.start_date == dsel).order_by(Task.id.asc()).all()

    
    SUSTAIN_CATS = [
        c for c in globals().get(
        "SUSTAIN_OPTIONS",
        ["節能", "節水", "低碳交通", "飲食", "資源回收"],
        )
        if str(c) != "其他"
    ]

    def is_mission_task(t: "Task") -> bool:
        return (getattr(t, "task_type", "homework") == "mission") or (
            (t.category or "其他") in SUSTAIN_CATS
        )

    
    hw_items = [t for t in day_tasks if not is_mission_task(t)]
    mission_items = [t for t in day_tasks if is_mission_task(t)]

    
    prompt = (
        DiaryPrompt.query.filter_by(
            unit=unit, grade=grade, class_no=class_no, date=dsel
        )
        .order_by(DiaryPrompt.id.desc())
        .first()
    )

    
    must_sign = bool(hw_items or mission_items or prompt)

    
    
    sig_map = {}
    if students:
        for s in students:
            sig = (
                ParentSignature.query.filter_by(
                    student_name=s.username, date=dsel, scope="homework"
                )
                .order_by(ParentSignature.id.desc())
                .first()
            )
            sig_map[s.username] = sig

    
    sub_map = {}
    if prompt:
        subs = DiarySubmission.query.filter_by(prompt_id=prompt.id).all()
        sub_map = {x.student_name: x for x in subs}

    
    
    def mission_status_chip(student_username: str, t: "Task") -> str:
        st, last = latest_status_for(student_username, t.id)
        title = f"{t.title}"
        if st == "approved":
            return (
                f"<span class='badge bg-success me-1' title='{title}'>核准</span>"
            )
        if st == "pending":
            return (
                f"<span class='badge bg-warning text-dark me-1' title='{title}'>待審</span>"
            )
        if st == "rejected":
            tip = (last.reject_reason or "已退回").replace("'", "&#39;")
            return (
                f"<span class='badge bg-danger me-1' "
                f"title='{title}｜{tip}'>退回</span>"
            )
        return (
            f"<span class='badge bg-secondary me-1' title='{title}'>未交</span>"
        )

    
    prev_d = (dsel - timedelta(days=1)).isoformat()
    next_d = (dsel + timedelta(days=1)).isoformat()
    nav = (
        "<div class='d-flex justify-content-between align-items-center mb-3 flex-wrap'>"
        f"<div class='me-2 mb-2'><a class='btn btn-sm btn-outline-secondary' href='{url_for('teacher_day', date=prev_d)}'>&laquo; 前一天</a></div>"
        "<form method='get' class='d-flex align-items-center gap-2 mb-2'>"
        f"<input type='date' name='date' class='form-control form-control-sm' value='{dsel}'>"
        "<button class='btn btn-sm btn-primary'>前往</button></form>"
        f"<div class='ms-2 mb-2'><a class='btn btn-sm btn-outline-secondary' href='{url_for('teacher_day', date=next_d)}'>下一天 &raquo;</a></div>"
        "</div>"
    )
    subtitle = (
        f"<div class='small text-muted mb-2'>{unit or ''} · {grade}年{class_no}班 · {dsel}</div>"
    )

    
    total_students = len(students)
    signed_count = sum(1 for s in students if sig_map.get(s.username))
    diary_count = (
        sum(1 for s in students if (sub_map.get(s.username) is not None))
        if prompt
        else 0
    )
    summary = (
        "<div class='alert alert-light border d-flex flex-wrap gap-3 align-items-center'>"
        f"<div><b>是否需要家長簽名：</b>{'是' if must_sign else '否'}</div>"
        f"<div><b>簽名完成：</b>{signed_count}/{total_students}</div>"
        f"<div><b>今日任務數：</b>{len(mission_items)}</div>"
        f"<div><b>日記繳交：</b>{diary_count}/{total_students if prompt else 0}{'' if prompt else '（今日未出題）'}</div>"
        "<div class='ms-auto small text-muted'>圖例："
        "<span class='badge bg-success'>核准</span> "
        "<span class='badge bg-warning text-dark'>待審</span> "
        "<span class='badge bg-danger'>退回</span> "
        "<span class='badge bg-secondary'>未交</span>"
        "</div>"
        "</div>"
    )

    
    def row_of(s: "User") -> str:
        
        sig = sig_map.get(s.username)
        if must_sign:
            sig_html = (
                "<div class='teacher-signature-preview'>"
                "<span class='badge bg-success'>已簽名</span>"
                f"{img_html(getattr(sig, 'image', None), maxw=180)}"
                "</div>"
                if sig
                else "<span class='badge bg-danger'>未簽名</span>"
            )
        else:
            sig_html = "<span class='badge bg-secondary'>（今日不需簽）</span>"

        
        if mission_items:
            chips = "".join(
                mission_status_chip(s.username, t) for t in mission_items
            )
        else:
            chips = "<span class='text-muted'>—</span>"

        
        if prompt:
            sub = sub_map.get(s.username)
            if not sub:
                diary_html = "<span class='badge bg-secondary'>未繳交</span>"
            else:
                diary_html = (
                    "<span class='badge bg-success'>已繳交（公開）</span>"
                    if sub.share_to_parent
                    else "<span class='badge bg-info text-dark'>已繳交（未公開）</span>"
                )
        else:
            diary_html = "<span class='text-muted'>未出題</span>"

        return (
            "<tr>"
            f"<td class='text-nowrap'>{display_name_of(s.username)}"
            f"<div class='small text-muted'>{s.username}</div></td>"
            f"<td class='text-nowrap'>{sig_html}</td>"
            f"<td class='text-nowrap'>{chips}</td>"
            f"<td class='text-nowrap'>{diary_html}</td>"
            "</tr>"
        )

    body = (
        "".join(row_of(s) for s in students)
        or "<tr><td colspan='4' class='text-center'>本班尚無學生</td></tr>"
    )
    table = (
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th>學童</th><th>家長簽名</th><th>任務</th><th>日記</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )

    
    if not (hw_items or mission_items or prompt):
        empty = (
            "<div class='text-center text-muted py-4'>今天沒有聯絡簿內容（不含簽名名單）</div>"
        )
        return page("老師｜班級日記總覽", subtitle + nav + summary + table + empty)

    
    
    def hw_block():
        if not hw_items:
            return ""
        by_cat = {}
        for t in hw_items:
            by_cat.setdefault(t.category or "其他", []).append(t)

        def cell(items):
            return "".join(
                f"<div class='mb-1'><b>{t.title}</b> "
                f"<small class='text-muted'>{t.description}</small></div>"
                for t in items
            )

        rows = "".join(
            f"<tr><th class='text-nowrap' style='width:8rem'>{c}</th>"
            f"<td>{cell(by_cat[c])}</td></tr>"
            for c in by_cat
        )
        return (
            "<h5 class='mt-4'>作業（僅閱讀）</h5>"
            "<div class='table-responsive'><table class='table table-sm align-middle'>"
            "<thead><tr><th style='width:8rem'>科目</th><th>內容</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )

    

    
    def diary_block():
        if not prompt:
            return ""
        thumb = img_html(diary_prompt_img_name(prompt), maxw=260)
        return (
            "<h5 class='mt-4'>日記</h5>"
            "<div class='p-3 border rounded'>"
            f"<div class='fw-bold'>{prompt.date}｜{prompt.title}</div>"
            f"{('<div class=\"small text-muted mt-1\">' + _user_text_html(prompt.instruction) + '</div>') if prompt.instruction else ''}"
            f"{thumb}"
            "</div>"
        )

    detail = hw_block() + diary_block()
    return page(
        "老師｜班級日記總覽",
        subtitle + nav + summary + table + (detail or ""),
    )

@app.route("/comm", methods=["GET", "POST"])
@login_required
def comm():
    """
    親師交流（統一頁）：
    - 老師/家長/學生都能發文
    - 只能指定個別學生（student_username 為必填）
    - 依使用者身分只顯示「看得到」的項目
    """
    
    dsel = parse_date((request.args.get("date") or "").strip()) or local_today()
    unit = current_user.unit

    
    def class_students(u: str, g: str, c: str):
        return (User.query
                .filter_by(role="student", unit=u, grade=g, class_no=c)
                .order_by(User.username.asc())
                .all())

    
    if request.method == "POST":
        role = current_user.role
        date = parse_date(g("date")) or dsel
        title = g("title")
        content = (g("content") or "")
        fs = request.files.get("image")
        saved = save_image(fs) if (fs and fs.filename.strip()) else None

        if role == "teacher":
            
            sid = (g("student_username") or "").strip()
            if not sid:
                return toast_redirect("comm", "請選擇一位學生。", "warning", date=date.isoformat())
            stu = (User.query
                   .filter_by(username=sid, role="student", unit=current_user.unit,
                              grade=current_user.grade, class_no=current_user.class_no)
                   .first())
            if not stu:
                return toast_redirect("comm", "指定學生不存在或不在你的班級。", "warning", date=date.isoformat())

            visible_to_student = 1 if request.form.get("visible_to_student") in ("1","on","true","True") else 0
            visible_to_parent  = 1  
            db.session.add(CommNote(
                date=date, title=title, content=content, image=saved,
                unit=unit, grade=stu.grade, class_no=stu.class_no,
                created_by=current_user.username, author_role="teacher",
                visible_to_student=visible_to_student, visible_to_parent=visible_to_parent,
                student_username=stu.username
            ))
            db.session.commit()
            return toast_redirect("comm", "已發布交流（老師）", "success", date=date.isoformat())

        elif role == "parent":
            
            sid = (g("student_username") or "").strip()
            bound = parent_child_query(student=sid).first()
            stu = User.query.filter_by(username=sid, role="student", unit=unit).first() if bound else None
            if not stu:
                return toast_redirect("comm", "請選擇你綁定的學童。", "warning", date=date.isoformat())

            visible_to_student = 1 if request.form.get("visible_to_student") in ("1","on","true","True") else 0
            db.session.add(CommNote(
                date=date, title=title, content=content, image=saved,
                unit=unit, grade=stu.grade, class_no=stu.class_no,
                created_by=current_user.username, author_role="parent",
                visible_to_student=visible_to_student, visible_to_parent=1,
                student_username=stu.username
            ))
            db.session.commit()
            return toast_redirect("comm", "已發布交流（家長）", "success", date=date.isoformat())

        elif role == "student":
            
            visible_to_parent = 1 if request.form.get("visible_to_parent") in ("1","on","true","True") else 0
            db.session.add(CommNote(
                date=date, title=title, content=content, image=saved,
                unit=unit, grade=current_user.grade, class_no=current_user.class_no,
                created_by=current_user.username, author_role="student",
                visible_to_student=1, visible_to_parent=visible_to_parent,
                student_username=current_user.username
            ))
            db.session.commit()
            return toast_redirect("comm", "已發布交流（學生）", "success", date=date.isoformat())

        else:
            return toast_redirect(role_endpoint(current_user.role), "此角色不支援交流發布。", "warning")

    
    def form_block() -> str:
        if current_user.role == "teacher":
            stus = class_students(current_user.unit, current_user.grade, current_user.class_no)
            stu_opts = "".join(
                f"<option value='{s.username}'>{s.display_name or s.username}（{s.username}）</option>"
                for s in stus
            ) or "<option value=''>（本班尚無學生）</option>"
            return (
                "<div class='card border-0 shadow-sm mb-3'><div class='card-body'>"
                "<h5 class='card-title mb-2'>老師發布交流</h5>"
                "<form method='post' enctype='multipart/form-data' class='row g-2'>"
                f"<input type='hidden' name='date' value='{dsel}'>"
                "<div class='col-md-4'><label class='form-label'>標題</label>"
                "<input name='title' class='form-control' required></div>"
                f"<div class='col-md-8'><label class='form-label'>指定學生</label>"
                f"<select name='student_username' class='form-select' required>{stu_opts}</select></div>"
                "<div class='col-12'><div class='setting-group'>"
                "<div class='setting-group__label'>可見對象設定</div>"
                f"{setting_checkbox_html('vis_stu', 'visible_to_student', '讓學生也看得到', '勾選後，這位學生可以在交流區看到本則內容。', checked=True)}"
                "</div></div>"
                "<div class='col-12'><label class='form-label'>交流內容</label>"
                "<textarea name='content' class='form-control' rows='3' placeholder='請寫下要提醒、回覆或補充的內容。'></textarea></div>"
                "<div class='col-md-6'><label class='form-label'>附加圖片（選填）</label>"
                "<input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"
                "<div class='col-12'><button class='btn btn-primary'>送出交流</button></div>"
                "</form></div></div>"
            )

        elif current_user.role == "parent":
            
            links = parent_child_links()
            kids = [User.query.filter_by(username=lk.student_name, role='student', unit=unit).first() for lk in links]
            kids = [k for k in kids if k]
            if not kids:
                return "<div class='alert alert-warning'>尚未綁定學童，無法發布交流。</div>"
            opts = "".join(
                f"<option value='{k.username}'>{k.display_name or k.username}（{k.grade}年{k.class_no}班）</option>"
                for k in kids
            )
            return (
                "<div class='card border-0 shadow-sm mb-3'><div class='card-body'>"
                "<h5 class='card-title mb-2'>家長發布交流</h5>"
                "<form method='post' enctype='multipart/form-data' class='row g-2'>"
                f"<input type='hidden' name='date' value='{dsel}'>"
                "<div class='col-md-4'><label class='form-label'>標題</label>"
                "<input name='title' class='form-control' required></div>"
                f"<div class='col-md-8'><label class='form-label'>指定學童</label>"
                f"<select name='student_username' class='form-select' required>{opts}</select></div>"
                "<div class='col-12'><div class='setting-group'>"
                "<div class='setting-group__label'>可見對象設定</div>"
                f"{setting_checkbox_html('vis_stu2', 'visible_to_student', '讓孩子也看得到', '勾選後，孩子可以在交流區看到這則內容。', checked=True)}"
                "</div></div>"
                "<div class='col-12'><label class='form-label'>交流內容</label>"
                "<textarea name='content' class='form-control' rows='3' placeholder='可補充今天的提醒、回覆或想和老師分享的內容。'></textarea></div>"
                "<div class='col-md-6'><label class='form-label'>附加圖片（選填）</label>"
                "<input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"
                "<div class='col-12'><button class='btn btn-primary'>送出交流</button></div>"
                "</form></div></div>"
            )

        elif current_user.role == "student":
            return (
                "<div class='card border-0 shadow-sm mb-3'><div class='card-body'>"
                "<h5 class='card-title mb-2'>學生發布交流</h5>"
                "<form method='post' enctype='multipart/form-data' class='row g-2'>"
                f"<input type='hidden' name='date' value='{dsel}'>"
                "<div class='col-md-12'><label class='form-label'>標題</label>"
                "<input name='title' class='form-control' required></div>"
                "<div class='col-12'><div class='setting-group'>"
                "<div class='setting-group__label'>可見對象設定</div>"
                f"{setting_checkbox_html('vis_par', 'visible_to_parent', '讓家長也看得到', '勾選後，家長可以看到這則交流內容。', checked=False)}"
                "</div></div>"
                "<div class='col-12'><label class='form-label'>交流內容</label>"
                "<textarea name='content' class='form-control' rows='3' placeholder='可以寫下今天想和老師或家長分享的內容。'></textarea></div>"
                "<div class='col-md-6'><label class='form-label'>附加圖片（選填）</label>"
                "<input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"
                "<div class='col-12'><button class='btn btn-primary'>送出交流</button></div>"
                "</form></div></div>"
            )
        return ""

    
    base = CommNote.query.filter(CommNote.unit == unit, CommNote.date == dsel).order_by(CommNote.id.desc()).all()

    def visible_notes_for_current_user(notes: list["CommNote"]) -> list["CommNote"]:
        role = current_user.role

        if role == "teacher":
            
            return [
                n for n in notes
                if n.grade == current_user.grade and n.class_no == current_user.class_no
                and n.student_username  
            ]

        elif role == "student":
            
            mine = current_user.username
            vis = []
            for n in notes:
                if n.student_username == mine:
                    if n.author_role == "teacher" and n.visible_to_student != 1: 
                        continue
                    if n.author_role == "parent"  and n.visible_to_student != 1: 
                        continue
                    vis.append(n)
            return vis

        elif role == "parent":
            
            kids = parent_child_student_usernames()
            if not kids:
                return []
            vis = []
            for n in notes:
                if n.student_username and n.student_username in kids:
                    if n.author_role == "student" and n.visible_to_parent != 1: 
                        continue
                    if n.author_role == "parent"  and n.created_by != current_user.username: 
                        continue
                    vis.append(n)
            return vis

        return []

    notes = visible_notes_for_current_user(base)

    
    role_badge = {
        "teacher":"<span class='badge bg-primary ms-1'>老師</span>",
        "parent":"<span class='badge bg-dark ms-1'>家長</span>",
        "student":"<span class='badge bg-success ms-1'>學生</span>"
    }

    def row(n: "CommNote"):
        title_txt = escape(n.title or "未命名交流")
        content_txt = _user_text_html(n.content)
        target = f"指定：{display_name_of(n.student_username)}"
        vis_tags = []
        if n.author_role in ("teacher", "parent"):
            vis_tags.append("學生可見" if n.visible_to_student else "學生不可見")
            if n.author_role == "teacher":
                vis_tags.append("家長可見")  
        elif n.author_role == "student":
            vis_tags.append("家長可見" if n.visible_to_parent else "家長不可見")

        img = img_html(getattr(n, "image", None), maxw=280) if getattr(n, "image", None) else ""

        can_del = (
            (current_user.role == "teacher" and n.grade == current_user.grade and n.class_no == current_user.class_no) or
            (current_user.username == n.created_by)
        )
        del_btn = (f"<a class='btn comm-soft-btn comm-soft-btn--danger' href='/comm_delete/{n.id}' "
                   f"onclick='return confirm(\"刪除此交流？\");'>刪除</a>") if can_del else ""
        tag_html = "".join(f"<span>{escape(tag)}</span>" for tag in vis_tags)

        return (
            "<article class='comm-note-card'>"
            "<div class='comm-note-main'>"
            "<div class='comm-note-top'>"
            f"<div><div class='comm-note-title'>{title_txt} {role_badge.get(n.author_role,'')}</div>"
            f"<div class='comm-note-meta'>{escape(str(n.date))} · {escape(display_name_of(n.created_by))} · {escape(target)}</div></div>"
            f"<div class='comm-note-tags'>{tag_html}</div>"
            "</div>"
            f"{('<div class=\"comm-note-content\">'+content_txt+'</div>') if (n.content or '').strip() else ''}"
            f"{img}"
            "</div>"
            f"<div class='comm-note-actions'>{del_btn}</div>"
            "</article>"
        )

    list_html = "".join(row(n) for n in notes) or "<div class='comm-empty'>這一天目前沒有交流紀錄。</div>"

    
    prev_d = (dsel - timedelta(days=1)).isoformat()
    next_d = (dsel + timedelta(days=1)).isoformat()
    nav = (
        "<div class='comm-date-nav'>"
        f"<a class='btn comm-soft-btn' href='{url_for('comm', date=prev_d)}'>前一天</a>"
        "<form method='get' class='comm-date-form'>"
        f"<input type='date' name='date' class='form-control' value='{dsel}'>"
        "<button class='btn comm-soft-btn comm-soft-btn--green'>前往</button></form>"
        f"<a class='btn comm-soft-btn' href='{url_for('comm', date=local_today().isoformat())}'>今天</a>"
        f"<a class='btn comm-soft-btn' href='{url_for('comm', date=next_d)}'>下一天</a>"
        "</div>"
    )

    role_title = {
        "teacher": "親師交流",
        "parent": "親師交流",
        "student": "親師交流",
    }.get(current_user.role, "親師交流")
    incoming_count = sum(1 for n in notes if n.created_by != current_user.username)
    image_count = sum(1 for n in notes if getattr(n, "image", None))
    target_count = len({n.student_username for n in notes if n.student_username})
    metric_html = (
        "<div class='comm-metric-grid'>"
        f"<div><span>當日交流</span><strong>{len(notes)}</strong></div>"
        f"<div><span>收到訊息</span><strong>{incoming_count}</strong></div>"
        f"<div><span>關聯學生</span><strong>{target_count}</strong></div>"
        f"<div><span>附圖紀錄</span><strong>{image_count}</strong></div>"
        "</div>"
    )
    comm_style = """
    <style>
    .comm-page{display:flex;flex-direction:column;gap:1rem;}
    .comm-hero{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;border-radius:30px;padding:1.35rem;background:linear-gradient(135deg,#ecfdf3 0%,#fff8e8 58%,#eef7ff 100%);border:1px solid rgba(35,92,59,.1);box-shadow:0 24px 60px rgba(24,76,46,.12);}
    .comm-kicker{font-size:.78rem;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#4f8f61;margin-bottom:.25rem;}
    .comm-hero h3{margin:0;color:#162318;font-weight:950;letter-spacing:-.02em;}
    .comm-muted{color:#647067;font-size:.94rem;line-height:1.65;}
    .comm-date-nav{display:flex;gap:.55rem;align-items:center;flex-wrap:wrap;justify-content:flex-end;}
    .comm-date-form{display:flex;gap:.45rem;align-items:center;}
    .comm-date-form .form-control{border-radius:999px;min-width:10rem;}
    .comm-soft-btn{border-radius:999px!important;border:1px solid rgba(35,92,59,.18)!important;background:#fff!important;color:#244c32!important;font-weight:850!important;padding:.48rem .9rem!important;box-shadow:0 10px 24px rgba(24,76,46,.08);}
    .comm-soft-btn--green{background:#34784a!important;color:#fff!important;border-color:#34784a!important;}
    .comm-soft-btn--danger{color:#a33a3a!important;border-color:rgba(163,58,58,.22)!important;}
    .comm-metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;}
    .comm-metric-grid>div{border-radius:20px;background:#fff;border:1px solid rgba(19,56,35,.08);box-shadow:0 12px 28px rgba(24,76,46,.06);padding:.9rem 1rem;}
    .comm-metric-grid span{display:block;color:#718078;font-size:.82rem;font-weight:900;}
    .comm-metric-grid strong{display:block;color:#183d28;font-size:1.5rem;line-height:1.1;font-weight:950;margin-top:.18rem;}
    .comm-form-panel,.comm-list-panel{border-radius:28px!important;overflow:hidden;}
    .comm-form-panel .card-body,.comm-list-panel .card-body{padding:1.15rem 1.25rem;}
    .comm-form-panel h5,.comm-list-panel h5{font-weight:950;color:#17231b;}
    .comm-list{display:flex;flex-direction:column;gap:.75rem;}
    .comm-note-card{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;border-radius:22px;background:linear-gradient(180deg,#fff 0%,#fbfff8 100%);border:1px solid rgba(19,56,35,.08);box-shadow:0 14px 30px rgba(24,76,46,.07);padding:1rem;}
    .comm-note-main{min-width:0;flex:1;}
    .comm-note-top{display:flex;justify-content:space-between;align-items:flex-start;gap:.8rem;flex-wrap:wrap;}
    .comm-note-title{font-size:1.08rem;font-weight:950;color:#17231b;}
    .comm-note-meta{color:#647067;font-size:.9rem;font-weight:800;margin-top:.18rem;}
    .comm-note-tags{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:flex-end;}
    .comm-note-tags span{display:inline-flex;border-radius:999px;background:#f4faf5;border:1px solid rgba(35,92,59,.1);color:#2f7446;font-size:.78rem;font-weight:850;padding:.2rem .5rem;}
    .comm-note-content{margin-top:.75rem;border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:.85rem 1rem;color:#24362c;line-height:1.8;}
    .comm-note-actions{display:flex;gap:.45rem;align-items:center;justify-content:flex-end;}
    .comm-empty{border-radius:20px;background:#f8fbf6;border:1px dashed rgba(35,92,59,.16);color:#718078;padding:1rem;}
    @media (max-width:991.98px){.comm-metric-grid{grid-template-columns:repeat(2,minmax(0,1fr));}.comm-date-nav{justify-content:flex-start;}}
    @media (max-width:767.98px){.comm-hero,.comm-form-panel,.comm-list-panel{border-radius:22px!important;}.comm-metric-grid{grid-template-columns:1fr;}.comm-date-form{width:100%;}.comm-date-form .form-control{flex:1;min-width:0;}.comm-note-actions{justify-content:flex-start;width:100%;}.comm-note-tags{justify-content:flex-start;}}
    </style>
    """

    return page(
        "親師交流",
        comm_style
        + "<div class='comm-page'>"
        + "<section class='comm-hero'>"
        + f"<div><div class='comm-kicker'>Communication</div><h3>{role_title}</h3><div class='comm-muted mt-1'>{escape(unit or '')} · {dsel}</div></div>"
        + nav
        + "</section>"
        + metric_html
        + form_block().replace("card border-0 shadow-sm mb-3", "card border-0 shadow-sm mb-3 comm-form-panel")
        + "<section class='card border-0 shadow-sm comm-list-panel'><div class='card-body'>"
        + "<div class='d-flex justify-content-between align-items-start gap-2 flex-wrap mb-3'>"
        + "<div><div class='comm-kicker'>Records</div><h5 class='mb-0'>交流紀錄</h5></div>"
        + f"<span class='badge bg-light text-muted border align-self-center'>{len(notes)} 筆</span>"
        + "</div>"
        + f"<div class='comm-list'>{list_html}</div>"
        + "</div></section></div>"
    )

@app.route("/comm_delete/<int:nid>")
@login_required
def comm_delete(nid):
    n = CommNote.query.get_or_404(nid)
    
    can = (
        (current_user.username == n.created_by) or
        (current_user.role == "teacher" and n.unit == current_user.unit and n.grade == current_user.grade and n.class_no == current_user.class_no)
    )
    if not can:
        return toast_redirect("comm", "你沒有權限刪除此交流。", "warning", date=n.date.isoformat())
    d = n.date
    db.session.delete(n)
    db.session.commit()
    return toast_redirect("comm", "已刪除交流。", "success", date=d.isoformat())


from datetime import datetime


def _esc(s: str) -> str:
    return _user_text_html(s)


def _get_submission_exact(prompt_id: int, student_name: str):
    """同題目同學生取最新一筆，避免舊資料被誤寫"""
    return (DiarySubmission.query
            .filter_by(prompt_id=prompt_id, student_name=student_name)
            .order_by(DiarySubmission.id.desc())
            .first())


def _set_teacher_comment(prompt: "DiaryPrompt", student: str, comment: str):
    """
    寫入/清除老師回饋（以最新一筆日記為準）
    傳回：(ok: bool, message: str, category: 'success'|'warning'|'danger')
    """
    
    if not (prompt.unit == current_user.unit and prompt.grade == current_user.grade and prompt.class_no == current_user.class_no):
        return False, "無權限操作其他班級。", "danger"

    sub = _get_submission_exact(prompt.id, student)
    if not sub:
        return False, "該學生尚未繳交此篇日記，無法儲存回饋。", "warning"

    comment = (comment or "").strip()

    if comment:
        sub.teacher_comment = comment
        try:
            sub.teacher_commented_at = datetime.utcnow()
        except Exception:
            pass
        
        for col in ("teacher_commented_by", "reviewed_by", "commented_by", "teacher_name"):
            if hasattr(sub, col):
                try:
                    setattr(sub, col, getattr(current_user, "username", None) or getattr(current_user, "id", None))
                except Exception:
                    pass
        db.session.add(sub)
        db.session.commit()
        return True, "回饋已儲存。", "success"

    
    sub.teacher_comment = ""
    try:
        sub.teacher_commented_at = None
    except Exception:
        pass
    for col in ("teacher_commented_by", "reviewed_by", "commented_by", "teacher_name"):
        if hasattr(sub, col):
            try:
                setattr(sub, col, None)
            except Exception:
                pass
    db.session.add(sub)
    db.session.commit()
    return True, "已刪除回饋。", "success"



@app.route("/teacher_diary_comment/<int:prompt_id>/<student>", methods=["POST"])
@roles_required("teacher")
def teacher_diary_comment(prompt_id: int, student: str):
    ensure_diary_submission_feedback_columns()
    prompt = DiaryPrompt.query.get_or_404(prompt_id)

    ok, msg, cat = _set_teacher_comment(prompt, student, request.form.get("comment"))
    return toast_redirect("teacher_diary_detail", msg, cat, prompt_id=prompt_id)



@app.route("/teacher_diary_comment_save/<int:prompt_id>/<student>", methods=["POST"])
@roles_required("teacher")
def teacher_diary_comment_save(prompt_id: int, student: str):
    ensure_diary_submission_feedback_columns()
    prompt = DiaryPrompt.query.get_or_404(prompt_id)

    ok, msg, cat = _set_teacher_comment(prompt, student, request.form.get("comment"))
    return toast_redirect("teacher_diary_detail", msg, cat, prompt_id=prompt_id)



@app.route("/teacher_diary_comment_delete/<int:prompt_id>/<student>", methods=["POST"])
@roles_required("teacher")
def teacher_diary_comment_delete(prompt_id: int, student: str):
    ensure_diary_submission_feedback_columns()
    prompt = DiaryPrompt.query.get_or_404(prompt_id)

    ok, msg, cat = _set_teacher_comment(prompt, student, "")
    return toast_redirect("teacher_diary_detail", msg, cat, prompt_id=prompt_id)



@app.route("/diary_view/<int:prompt_id>")
@login_required
def diary_view(prompt_id: int):
    ensure_diary_submission_feedback_columns()

    prompt = DiaryPrompt.query.get_or_404(prompt_id)

    
    role = getattr(current_user, "role", "")
    target_student = (request.args.get("student") or "").strip()
    if role == "student":
        target_student = current_user.username
    elif role == "teacher":
        if not (prompt.unit == current_user.unit and prompt.grade == current_user.grade and prompt.class_no == current_user.class_no):
            return page("無權限", "<div class='alert alert-danger'>你無權查看其他班級的日記。</div>")
        if not target_student:
            return page("資料不足", "<div class='alert alert-warning'>缺少學生資料，請回上一頁重新操作。</div>")
    elif role == "parent":
        if not target_student:
            return page("資料不足", "<div class='alert alert-warning'>缺少學生資料，請回上一頁重新操作。</div>")
        kids = set(parent_child_student_usernames())
        if target_student not in kids:
            return page("無權限", "<div class='alert alert-danger'>只能查看自己孩子的日記。</div>")
    else:
        return page("無權限", "<div class='alert alert-danger'>請以師生或家長身份登入。</div>")

    stu = User.query.filter_by(username=target_student, role="student").first()
    if not stu:
        return page("無此學生", "<div class='alert alert-danger'>查無此學生。</div>")

    
    sub = _get_submission_exact(prompt.id, target_student)

    if role == "parent":
        if not sub or getattr(sub, "share_to_parent", 0) != 1:
            instruction_html = (
                f"<div class='parent-diary-prompt-note'>{_user_text_html(prompt.instruction)}</div>"
                if getattr(prompt, "instruction", None)
                else "<div class='parent-diary-prompt-note parent-diary-prompt-note--empty'>老師沒有補充說明。</div>"
            )
            prompt_img = img_html(diary_prompt_img_name(prompt), maxw=320)
            state_text = "孩子尚未繳交這篇日記。" if not sub else "孩子已繳交，但尚未開放家長閱讀。"
            body = (
                "<div class='parent-diary-page'>"
                "<article class='parent-diary-card' style='display:block;'>"
                "<div class='parent-diary-card__main'>"
                "<div class='parent-diary-card__head'>"
                "<div>"
                "<div class='parent-diary-section-label'>老師題目</div>"
                f"<h3 class='parent-diary-title'>{escape(prompt.title or '（無標題）')}</h3>"
                "</div>"
                f"<div class='parent-diary-date'>{prompt.date}</div>"
                "</div>"
                f"{instruction_html}"
                f"{prompt_img}"
                "<section class='parent-diary-locked parent-diary-locked--private'>"
                "<div class='parent-diary-section-label'>孩子日記</div>"
                f"<div class='parent-diary-locked-title'>{state_text}</div>"
                "<div class='parent-diary-muted'>目前仍可查看老師發布的題目與說明；孩子開放後，日記全文會顯示在家長工作頁的日記區。</div>"
                "</section>"
                f"<div class='parent-diary-actions'><a class='btn btn-outline-secondary btn-sm' href='/parent?date={prompt.date}#parent-diary-panel'>回家長工作頁</a></div>"
                "</div></article></div>"
            )
            return page("家長｜日記題目", body)

    title = f"{prompt.date}｜{prompt.title}｜{stu.display_name or stu.username}"

    
    if not sub:
        body = f"<div class='h5 mb-2'>{title}</div><div class='alert alert-secondary'>尚未繳交</div>"
        return page("日記全文", body)

    content_html = _esc(sub.content or "").replace("\n", "<br>")
    prompt_img = img_html(diary_prompt_img_name(prompt), maxw=300)
    sub_img = img_html(getattr(sub, "image", None), maxw=300)
    raw_comment = getattr(sub, "teacher_comment", "")
    has_comment = bool(isinstance(raw_comment, str) and raw_comment.strip())
    comment_html = _esc(raw_comment).replace("\n", "<br>") if has_comment else ""

    meta = [f"繳交者：{stu.display_name or stu.username}"]
    if getattr(sub, "share_to_parent", 0) == 1:
        meta.append("對家長公開")
    if has_comment and getattr(sub, "teacher_commented_at", None):
        meta.append(f"老師回饋時間：{sub.teacher_commented_at}")

    
    delete_btn = ""
    if role == "teacher" and has_comment:
        delete_btn = (
            f"<form method='post' class='mt-2' "
            f"action='{url_for('teacher_diary_comment_delete', prompt_id=prompt.id, student=stu.username)}' "
            "onsubmit=\"return confirm('確定刪除這則回饋？');\">"
            "<button class='btn btn-sm btn-outline-danger'>刪除回饋</button>"
            "</form>"
        )

    feedback_block = (
        "<div class='parent-diary-teacher-note'>"
        "<div class='parent-diary-section-label'>老師回饋</div>"
        f"<div>{comment_html}{delete_btn}</div>"
        "</div>"
    ) if has_comment else ""

    prompt_block = (
        "<section class='parent-diary-prompt-panel'>"
        "<div class='parent-diary-prompt-head'>"
        "<div>"
        "<div class='parent-diary-section-label'>老師題目</div>"
        f"<h3 class='parent-diary-title'>{escape(prompt.title or '（無標題）')}</h3>"
        "</div>"
        f"<div class='parent-diary-prompt-meta'>{prompt.date}</div>"
        "</div>"
                f"{('<div class=\"parent-diary-prompt-note\">' + _user_text_html(prompt.instruction) + '</div>') if getattr(prompt, 'instruction', None) else '<div class=\"parent-diary-prompt-note parent-diary-prompt-note--empty\">老師沒有補充說明。</div>'}"
        f"{prompt_img}"
        "</section>"
    )
    body = (
        "<div class='parent-diary-page'>"
        "<article class='parent-diary-card' style='display:block;'>"
        "<div class='parent-diary-card__main'>"
        "<div class='parent-diary-card__head'>"
        "<div>"
        "<div class='parent-diary-section-label'>日記全文</div>"
        f"<h3 class='parent-diary-title'>{escape(stu.display_name or stu.username)} 的日記</h3>"
        "</div>"
        f"<div class='parent-diary-date'>{' · '.join(meta)}</div>"
        "</div>"
        f"{prompt_block}"
        "<section class='parent-diary-entry'>"
        "<div class='parent-diary-entry-head'>"
        "<div><div class='parent-diary-section-label'>孩子日記</div>"
        f"<div class='parent-diary-entry-title'>{escape(stu.display_name or stu.username)} 的回覆</div></div>"
        "</div>"
        "<div class='parent-diary-content'>"
        f"<article class='parent-diary-text'>{content_html or '（無內容）'}</article>"
        f"{sub_img}"
        "</div>"
        "</section>"
        f"{feedback_block}"
        "</div></article></div>"
    )
    return page("日記全文", body)



@app.route("/teacher_diary_detail/<int:prompt_id>", methods=["GET"])
@roles_required("teacher")
def teacher_diary_detail(prompt_id: int):
    ensure_diary_submission_feedback_columns()

    prompt = DiaryPrompt.query.get_or_404(prompt_id)
    if not (prompt.unit == current_user.unit and prompt.grade == current_user.grade and prompt.class_no == current_user.class_no):
        return page("無權限", "<div class='alert alert-danger'>你無權查看其他班級的日記。</div>")

    students = (User.query
                .filter_by(role="student", unit=prompt.unit, grade=prompt.grade, class_no=prompt.class_no)
                .order_by(User.username.asc()).all())

    
    subs = (DiarySubmission.query
            .filter_by(prompt_id=prompt.id)
            .order_by(DiarySubmission.student_name.asc(), DiarySubmission.id.desc())
            .all())
    sub_map = {}
    for s in subs:
        sub_map.setdefault(s.student_name, s)  

    def status_badge(s):
        if not s:
            return "<span class='badge bg-secondary'>未繳交</span>"
        if getattr(s, "share_to_parent", 0) == 1:
            return "<span class='badge bg-success'>已繳交（對家長公開）</span>"
        return "<span class='badge bg-primary'>已繳交（未公開）</span>"

    def preview_text(s):
        if not s or not (s.content or "").strip():
            return "<span class='text-muted'>（無內容）</span>"
        txt = _esc((s.content or "").strip())
        return txt[:120] + ("..." if len(txt) > 120 else "")

    def comment_badge(s):
        txt = (getattr(s, "teacher_comment", None) or "")
        return "<span class='badge bg-dark ms-2'>已有回饋</span>" if (isinstance(txt, str) and txt.strip()) else ""

    def card_for(stu: "User"):
        s = sub_map.get(stu.username)
        name = stu.display_name or stu.username
        sub_thumb = img_html(getattr(s, "image", None), maxw=220) if s else ""

        view_btn = (f"<a class='btn btn-sm btn-outline-secondary me-2' "
                    f"href='{url_for('diary_view', prompt_id=prompt.id)}?student={stu.username}'>查看全文</a>") if s else ""

        form = ""
        if s:
            old = _esc((getattr(s, "teacher_comment", "") or "").strip())
            has_comment = bool(old)
            form = (
                f"<form method='post' action='{url_for('teacher_diary_comment_save', prompt_id=prompt.id, student=stu.username)}' class='mt-2'>"
                "<div class='form-floating'>"
                f"<textarea class='form-control' name='comment' id='c_{prompt.id}_{stu.username}' style='height:100px' "
                "placeholder='寫下給孩子的回饋（提交後學生可看到）'>"
                f"{old}</textarea>"
                f"<label for='c_{prompt.id}_{stu.username}'>老師回饋</label></div>"
                "<div class='mt-2 d-flex justify-content-end gap-2'>"
                "<button class='btn btn-sm btn-primary'>儲存回饋</button>"
                "</div></form>"
            )
            if has_comment:
                form += (
                    f"<form method='post' class='mt-2 text-end' "
                    f"action='{url_for('teacher_diary_comment_delete', prompt_id=prompt.id, student=stu.username)}' "
                    "onsubmit=\"return confirm('確定刪除這位學生的回饋？');\">"
                    "<button class='btn btn-sm btn-outline-danger'>刪除回饋</button>"
                    "</form>"
                )

        return (
            "<div class='card mb-3'><div class='card-body'>"
            f"<div class='d-flex justify-content-between align-items-center'>"
            f"<div class='h6 mb-0'>{name} {status_badge(s)}{comment_badge(s)}</div>"
            f"<div>{view_btn}</div>"
            "</div>"
            f"<div class='small text-muted mt-2'>{preview_text(s)}</div>"
            f"{sub_thumb}"
            f"{form}"
            "</div></div>"
        )

    body = "".join(card_for(s) for s in students) or "<div class='alert alert-secondary'>本班尚無學生</div>"

    head = (
        f"<div class='mb-2'><a class='btn btn-sm btn-outline-secondary' href='/teacher_diary'>&laquo; 返回題目列表</a></div>"
        f"<div class='h5 mb-1'>{prompt.date}｜{prompt.title}</div>"
        f"{('<div class=\"text-muted mb-3\">' + _user_text_html(prompt.instruction) + '</div>') if prompt.instruction else ''}"
        f"{img_html(diary_prompt_img_name(prompt), maxw=300)}"
    )
    return page("老師｜日記繳交概況", head + body)



@app.route("/teacher_diary_delete/<int:pid>")
@roles_required("teacher")
def teacher_diary_delete(pid):
    p = DiaryPrompt.query.get_or_404(pid)
    
    if not (p.unit == current_user.unit and p.grade == current_user.grade and p.class_no == current_user.class_no):
        return toast_redirect("teacher_diary", "無權刪除此題目。", "warning")
    DiarySubmission.query.filter_by(prompt_id=pid).delete()
    db.session.delete(p)
    db.session.commit()
    return toast_redirect("teacher_diary", "已刪除題目與其所有日記。", "success")


@app.route("/semester/weeks")
@login_required
def semester_weeks_current():
    if not _has_role("leader", "admin"):
        from flask import flash
        flash(f"需要組長或管理員權限（目前權限：{(getattr(current_user,'role','') or '').strip()}）。", "warning")
        return redirect(url_for("profile"))
    sem = current_semester(SCHOOL_NAME)
    if not sem:
        flash("目前不在任何學期期間內，請先到「學期管理」建立學期。", "warning")
        return redirect(url_for("semester_manage"))
    return redirect(url_for("semester_weeks", sid=sem.id))


def _has_role(*roles) -> bool:
    role_now = (getattr(current_user, "role", "") or "").strip().lower()
    want = {r.strip().lower() for r in roles}
    return role_now in want


def semester_weeks_view(sid):
    if not _has_role("leader", "admin"):
        return redirect(url_for("profile"))

    sem = db.session.get(Semester, sid)
    if not sem or sem.unit != SCHOOL_NAME:
        flash("學期不存在", "warning")
        return redirect(url_for("semester_manage"))

    def _overlap(ws, we, others):
        for o in others:
            if not (we < o.start_date or o.end_date < ws):
                return True
        return False

    if request.method == "POST":
        try:
            no = int((request.form.get("week_no") or "0"))
        except Exception:
            no = 0
        ws = parse_date(request.form.get("start_date"))
        we = parse_date(request.form.get("end_date"))
        if not (no and ws and we and ws <= we):
            flash("請填正確的週次與日期", "warning")
            return redirect(url_for("semester_weeks", sid=sid))
        if not (sem.start_date <= ws <= sem.end_date and sem.start_date <= we <= sem.end_date):
            flash("週次日期必須落在學期範圍內", "warning")
            return redirect(url_for("semester_weeks", sid=sid))

        others = (SemesterWeek.query
                  .filter_by(semester_id=sid)
                  .filter(SemesterWeek.week_no != no)
                  .all())
        if _overlap(ws, we, others):
            flash("週次日期區間不可與其他週次重疊", "warning")
            return redirect(url_for("semester_weeks", sid=sid))

        rec = SemesterWeek.query.filter_by(semester_id=sid, week_no=no).first()
        if rec:
            rec.start_date, rec.end_date = ws, we
        else:
            db.session.add(SemesterWeek(semester_id=sid, week_no=no, start_date=ws, end_date=we))
        db.session.commit()
        flash("已儲存週次", "success")
        return redirect(url_for("semester_weeks", sid=sid))

    wks = (SemesterWeek.query.filter_by(semester_id=sid)
           .order_by(SemesterWeek.week_no.asc()).all())
    rows = "".join(
        f"<tr><td>{w.week_no}</td><td>{w.start_date} ~ {w.end_date}</td>"
        f"<td><a class='btn btn-sm btn-outline-danger' href='{url_for('semester_week_delete', sid=sid, wid=w.id)}' "
        "onclick=\"return confirm('確定刪除此週次？');\">刪除</a></td></tr>"
        for w in wks
    )

    cal_imgs = (SemesterCalendarImage.query
                .filter_by(semester_id=sid)
                .order_by(SemesterCalendarImage.created_at.desc())
                .all())
    cal_grid = "".join(
        "<div class='col-md-4 col-lg-3'>"
        "<div class='card p-2 h-100'>"
        f"<div class='small text-muted mb-1'>{im.created_at.strftime('%Y-%m-%d')}</div>"
        f"{img_html(im.file, maxw=320)}"
        "</div></div>"
        for im in cal_imgs
    ) or "<div class='text-muted'>尚無行事曆圖片</div>"

    html = (
        f"<div class='alert alert-info'>學期：<b>{sem.name}</b>（{sem.start_date} ~ {sem.end_date}）</div>"
        "<h6 class='mb-2'>新增/覆蓋週次</h6>"
        "<form method='post' class='row g-2 mb-4'>"
        "<div class='col-2'><input type='number' min='1' class='form-control' name='week_no' placeholder='週次' required></div>"
        "<div class='col-3'><input type='date' class='form-control' name='start_date' required></div>"
        "<div class='col-3'><input type='date' class='form-control' name='end_date' required></div>"
        "<div class='col-2'><button class='btn btn-success w-100'>儲存</button></div>"
        "</form>"
        "<div class='table-responsive mb-4'><table class='table table-sm'>"
        "<thead><tr><th>週</th><th>起訖</th><th>操作</th></tr></thead><tbody>"
        + rows + "</tbody></table></div>"
        "<div class='d-flex align-items-center justify-content-between mb-2'>"
        "<h6 class='m-0'>本學期行事曆</h6>"
        f"<a class='btn btn-sm btn-outline-primary' href='{url_for('semester_calendar', sid=sid)}'>上傳 / 管理行事曆圖片</a>"
        "</div>"
        f"<div class='row g-3'>{cal_grid}</div>"
        f"<div class='mt-3'><a class='btn btn-outline-secondary' href='{url_for('semester_manage')}'>返回學期列表</a></div>"
    )
    return page("週次管理", shortcut_buttons_html() + html)


if 'semester_weeks' in app.view_functions:
    app.view_functions['semester_weeks'] = semester_weeks_view
else:
    app.add_url_rule(
        "/semester/<int:sid>/weeks",
        endpoint="semester_weeks",
        view_func=semester_weeks_view,
        methods=["GET", "POST"],
    )




@app.route("/semester/<int:sid>/edit", methods=["GET", "POST"])
@login_required
def semester_edit(sid):
    if current_user.role not in ("leader", "admin"):
        return redirect(url_for("profile"))

    sem = db.session.get(Semester, sid)
    if not sem or sem.unit != SCHOOL_NAME:
        flash("學期不存在", "warning")
        return redirect(url_for("semester_manage"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        sd = parse_date(request.form.get("start_date"))
        ed = parse_date(request.form.get("end_date"))
        if not (name and sd and ed and sd <= ed):
            flash("請輸入正確的學期名稱與起訖日期", "warning")
            return redirect(url_for("semester_edit", sid=sid))

        
        weeks = SemesterWeek.query.filter_by(semester_id=sid).all()
        out_of_range = [w for w in weeks if not (sd <= w.start_date <= ed and sd <= w.end_date <= ed)]
        if out_of_range:
            flash("有週次日期不在新的學期範圍內，請先調整/刪除週次後再改學期日期。", "warning")
            return redirect(url_for("semester_edit", sid=sid))

        sem.name, sem.start_date, sem.end_date = name, sd, ed
        db.session.commit()
        flash("學期已更新。", "success")
        return redirect(url_for("semester_manage"))

    form = (
        f"<div class='alert alert-info'>目前：<b>{sem.name}</b>（{sem.start_date} ~ {sem.end_date}）</div>"
        "<form method='post' class='row g-2 mb-4'>"
        f"<div class='col-md-3'><input class='form-control' name='name' value='{sem.name}' required></div>"
        f"<div class='col-md-3'><input type='date' class='form-control' name='start_date' value='{sem.start_date}' required></div>"
        f"<div class='col-md-3'><input type='date' class='form-control' name='end_date' value='{sem.end_date}' required></div>"
        "<div class='col-md-3'><button class='btn btn-primary w-100'>儲存</button></div>"
        "</form>"
        f"<a class='btn btn-outline-secondary' href='{url_for('semester_manage')}'>返回學期列表</a>"
    )
    return page("編輯學期", shortcut_buttons_html() + form)





@app.route("/semester/<int:sid>/delete")
@login_required
def semester_delete(sid):
    if current_user.role not in ("leader", "admin"):
        return redirect(url_for("profile"))

    sem = db.session.get(Semester, sid)
    if not sem or sem.unit != SCHOOL_NAME:
        flash("學期不存在", "warning")
        return redirect(url_for("semester_manage"))

    
    imgs = SemesterCalendarImage.query.filter_by(semester_id=sid).all()
    for im in imgs:
        try:
            os.remove(os.path.join(SEMESTER_DIR, im.file))
        except Exception:
            pass
        db.session.delete(im)

    
    SemesterWeek.query.filter_by(semester_id=sid).delete()

    
    db.session.delete(sem)
    db.session.commit()
    flash("已刪除學期與其週次、行事曆圖片。", "success")
    return redirect(url_for("semester_manage"))

@app.route("/semester/<int:sid>/weeks/delete/<int:wid>")
@login_required
def semester_week_delete(sid, wid):
    if current_user.role not in ("leader", "admin"):
        return redirect(url_for("profile"))

    sem = db.session.get(Semester, sid)
    if not sem or sem.unit != SCHOOL_NAME:
        flash("學期不存在", "warning")
        return redirect(url_for("semester_manage"))

    wk = db.session.get(SemesterWeek, wid)
    if not wk or wk.semester_id != sid:
        flash("週次不存在", "warning")
        return redirect(url_for("semester_weeks", sid=sid))

    db.session.delete(wk)
    db.session.commit()
    flash("已刪除週次", "success")
    return redirect(url_for("semester_weeks", sid=sid))





@app.route("/semester/<int:sid>/calendar", methods=["GET", "POST"])
@login_required
def semester_calendar(sid):
    if current_user.role not in ("leader", "admin"):
        return redirect(url_for("profile"))

    sem = db.session.get(Semester, sid)
    if not sem or sem.unit != SCHOOL_NAME:
        flash("學期不存在", "warning")
        return redirect(url_for("semester_manage"))

    if request.method == "POST":
        files = request.files.getlist("files")
        saved = _save_images(files, subdir="semester")
        if not saved:
            flash("未選擇檔案或格式不支援", "warning")
            return redirect(url_for("semester_calendar", sid=sid))
        for name in saved:
            db.session.add(SemesterCalendarImage(
                semester_id=sid, unit=SCHOOL_NAME, file=name, uploader=current_user.username
            ))
        db.session.commit()
        flash(f"已上傳 {len(saved)} 張行事曆圖片", "success")
        return redirect(url_for("semester_calendar", sid=sid))

    imgs = (SemesterCalendarImage.query
            .filter_by(semester_id=sid)
            .order_by(SemesterCalendarImage.created_at.desc())
            .all())

    grid = "".join(
        "<div class='col-md-4 col-lg-3'>"
        "<div class='card p-2 h-100'>"
        f"<div class='small text-muted mb-1'>{im.created_at.strftime('%Y-%m-%d')}</div>"
        f"{img_html(im.file, maxw=320)}"
        f"<a class='btn btn-sm btn-outline-danger' href='{url_for('semester_calendar_delete', iid=im.id)}' "
        "onclick=\"return confirm('刪除此圖片？');\">刪除</a>"
        "</div></div>"
        for im in imgs
    ) or "<div class='text-muted'>尚無行事曆圖片</div>"

    html = (
        f"<div class='alert alert-info'>學期：<b>{sem.name}</b>（{sem.start_date} ~ {sem.end_date}）</div>"
        "<div class='card p-3 mb-4'>"
        "<div class='fw-bold mb-2'>上傳行事曆圖片（可多張）</div>"
        "<form method='post' enctype='multipart/form-data' class='row g-2'>"
        "<div class='col-md-8'><input class='form-control' type='file' name='files' accept='.png,.jpg,.jpeg,.gif' multiple required></div>"
        "<div class='col-md-4'><button class='btn btn-primary w-100'>上傳</button></div>"
        "</form>"
        "</div>"
        "<h6 class='mb-2'>已上傳</h6>"
        f"<div class='row g-3'>{grid}</div>"
        f"<div class='mt-3'><a class='btn btn-outline-secondary' href='{url_for('semester_manage')}'>返回學期列表</a></div>"
    )
    return page("學期行事曆", shortcut_buttons_html() + html)

@app.route("/semester", methods=["GET","POST"])
@login_required
def semester_manage():
    if current_user.role not in ("leader","admin"):
        return redirect(url_for("profile"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        sd = parse_date(request.form.get("start_date"))
        ed = parse_date(request.form.get("end_date"))
        if not (name and sd and ed and sd <= ed):
            flash("請輸入正確的學期名稱與起訖日期", "warning")
            return redirect(url_for("semester_manage"))
        sem = Semester(unit=SCHOOL_NAME, name=name, start_date=sd, end_date=ed, created_by=current_user.username)
        db.session.add(sem); db.session.commit()
        
        for no, ws, we in _week_spans(sem):
            db.session.add(SemesterWeek(semester_id=sem.id, week_no=no, start_date=ws, end_date=we))
        db.session.commit()
        flash(f"已建立學期「{name}」與週次", "success")
        return redirect(url_for("semester_manage"))

    rows = (Semester.query.filter_by(unit=SCHOOL_NAME)
            .order_by(Semester.start_date.desc()).all())
    body = []
    for s in rows:
        body.append(
            "<tr>"
            f"<td>{s.name}</td>"
            f"<td>{s.start_date} ~ {s.end_date}</td>"
            f"<td class='text-nowrap'>"
            f"<a class='btn btn-sm btn-outline-primary me-2' href='{url_for('semester_weeks', sid=s.id)}'>週次</a>"
            f"<a class='btn btn-sm btn-outline-secondary me-2' href='{url_for('semester_edit', sid=s.id)}'>編輯</a>"
            f"<a class='btn btn-sm btn-outline-danger' href='{url_for('semester_delete', sid=s.id)}' "
            "onclick=\"return confirm('確定刪除此學期（含週次與行事曆）？');\">刪除</a>"
            "</td>"
            "</tr>"
        )

    html = (
        
        "<div class='d-flex align-items-center justify-content-between mb-3'>"
        "<h5 class='m-0'>新增學期</h5>"
        f"<a class='btn btn-warning' href='{url_for('semester_weeks_current')}'>前往當前學期週次</a>"
        "</div>"

        "<form method='post' class='row g-2 mb-4'>"
        "<div class='col-md-3'><input class='form-control' name='name' placeholder='例：113-上' required></div>"
        "<div class='col-md-3'><input type='date' class='form-control' name='start_date' required></div>"
        "<div class='col-md-3'><input type='date' class='form-control' name='end_date' required></div>"
        "<div class='col-md-3'><button class='btn btn-success w-100'>建立</button></div>"
        "</form>"

        "<h5 class='mb-2'>已建立</h5>"
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th>學期</th><th>起訖</th><th>管理</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table></div>"
    )
    return page("學期管理", shortcut_buttons_html() + html)

@app.route("/semester/calendar/delete/<int:iid>")
@login_required
def semester_calendar_delete(iid):
    if current_user.role not in ("leader", "admin"):
        return redirect(url_for("profile"))

    im = db.session.get(SemesterCalendarImage, iid)
    if not im:
        flash("圖片不存在", "warning")
        return redirect(url_for("semester_manage"))

    
    try:
        os.remove(os.path.join(SEMESTER_DIR, im.file))
    except Exception:
        pass

    sid = im.semester_id
    db.session.delete(im)
    db.session.commit()
    flash("已刪除圖片", "success")
    return redirect(url_for("semester_calendar", sid=sid))




@app.route("/timetable/images", methods=["GET"])
@login_required
def timetable_images():
    
    teacher_imgs = []
    class_imgs = []

    if current_user.role in ("teacher", "leader", "admin"):
        teacher_imgs = (
            TimetableImage.query.filter_by(
                unit=SCHOOL_NAME, kind="teacher", owner_username=current_user.username
            )
            .order_by(TimetableImage.created_at.desc())
            .all()
        )

        
        scopes = set()
        if current_user.role in ("leader", "admin"):
            scopes = {(g, c) for g in GRADE_OPTIONS for c in CLASS_OPTIONS}
        else:
            scopes = _teacher_scopes(current_user)
            if is_homeroom_of(current_user, current_user.grade, current_user.class_no):
                scopes.add((current_user.grade, current_user.class_no))

        if scopes:
            class_imgs = (
                TimetableImage.query.filter_by(unit=SCHOOL_NAME, kind="class")
                .filter(
                    TimetableImage.grade.in_([str(g) for g, _ in scopes]),
                    TimetableImage.class_no.in_([str(c) for _, c in scopes]),
                )
                .order_by(TimetableImage.created_at.desc())
                .all()
            )
        else:
            class_imgs = []

    allowed_classes = []
    if current_user.role in ("leader", "admin"):
        allowed_classes = [(g, c) for g in GRADE_OPTIONS for c in CLASS_OPTIONS]
    elif current_user.role == "teacher":
        allowed_set = _teacher_scopes(current_user)
        if is_homeroom_of(current_user, current_user.grade, current_user.class_no):
            allowed_set.add((current_user.grade, current_user.class_no))
        allowed_classes = sorted(allowed_set, key=lambda x: (_safe_int(x[0], 999), _safe_int(x[1], 999)))

    class_options = "".join(
        f"<option value='{g}|{c}'>{g}年{c}班</option>"
        for g, c in allowed_classes
    ) or "<option value=''>目前沒有可管理的班級</option>"

    hero = f"""
    <section class='timetable-hero'>
      <div>
        <div class='timetable-eyebrow'>課表管理</div>
        <h2>課表圖片管理</h2>
      </div>
      <div class='timetable-stats'>
        <div><strong>{len(teacher_imgs)}</strong><span>教師課表</span></div>
        <div><strong>{len(class_imgs)}</strong><span>班級課表</span></div>
      </div>
    </section>
    """

    upload_panels = ""
    if current_user.role in ("teacher", "leader", "admin"):
        upload_panels = f"""
        <section class='timetable-upload-grid'>
          <div class='timetable-upload-card'>
            <div class='timetable-upload-icon'>T</div>
            <div>
              <h3>我的教師課表</h3>
            </div>
            <form method='post' action='{url_for('timetable_images_upload')}' enctype='multipart/form-data' class='timetable-upload-form'>
              <input type='hidden' name='kind' value='teacher'>
              <input class='form-control' type='file' name='files' accept='.png,.jpg,.jpeg,.gif' multiple required>
              <button class='btn btn-primary'>上傳教師課表</button>
            </form>
          </div>
          <div class='timetable-upload-card'>
            <div class='timetable-upload-icon timetable-upload-icon--class'>C</div>
            <div>
              <h3>班級課表</h3>
            </div>
            <form method='post' action='{url_for('timetable_images_upload')}' enctype='multipart/form-data' class='timetable-upload-form'>
              <input type='hidden' name='kind' value='class'>
              <select class='form-select' name='class_combo' required>{class_options}</select>
              <input class='form-control' type='file' name='files' accept='.png,.jpg,.jpeg,.gif' multiple required>
              <button class='btn btn-primary' {'disabled' if not allowed_classes else ''}>上傳班級課表</button>
            </form>
          </div>
        </section>
        """

    def card_for(img: TimetableImage):
        info = f"教師：{img.owner_username}" if img.kind == "teacher" else f"班級：{img.grade}年{img.class_no}班"
        return (
            "<article class='timetable-image-card'>"
            f"<a href='{url_for('uploaded_file', filename=img.file)}' target='_blank'>"
            f"<img src='{url_for('uploaded_file', filename=img.file)}' alt='{escape(info)}'>"
            "</a>"
            "<div class='timetable-image-meta'>"
            f"<div><strong>{info}</strong><span>{img.created_at.strftime('%Y-%m-%d')}</span></div>"
            f"<a class='btn btn-sm btn-outline-danger' href='{url_for('timetable_images_delete', iid=img.id)}' onclick='return confirm(\"刪除此課表圖片？\");'>刪除</a>"
            "</div>"
            "</article>"
        )

    teacher_section = (
        "<section class='timetable-section'><div class='timetable-section-head'><h3>我的教師課表</h3></div>"
        + ("<div class='timetable-image-grid'>" + "".join(card_for(x) for x in teacher_imgs) + "</div>" if teacher_imgs else "<div class='timetable-empty'>尚無教師課表圖片。</div>")
        + "</section>"
    )
    class_section = (
        "<section class='timetable-section'><div class='timetable-section-head'><h3>班級課表</h3></div>"
        + ("<div class='timetable-image-grid'>" + "".join(card_for(x) for x in class_imgs) + "</div>" if class_imgs else "<div class='timetable-empty'>尚無可查看的班級課表圖片。</div>")
        + "</section>"
    )

    return page("課表圖片管理", shortcut_buttons_html() + f"<div class='timetable-page'>{hero}{upload_panels}{teacher_section}{class_section}</div>", links=[("返回個人檔案", url_for("profile"))])


@app.route("/timetable/images/upload", methods=["POST"])
@login_required
def timetable_images_upload():
    kind = (request.form.get("kind") or "").strip()
    files = request.files.getlist("files")
    saved = _save_images(files, subdir="timetable")
    if not saved:
        flash("未選擇檔案或格式不支援", "warning")
        return redirect(url_for("timetable_images"))

    if kind == "teacher":
        
        for name in saved:
            db.session.add(
                TimetableImage(
                    unit=SCHOOL_NAME,
                    kind="teacher",
                    file=name,
                    owner_username=current_user.username,
                    uploader=current_user.username,
                )
            )
        db.session.commit()
        flash(f"已上傳 {len(saved)} 張教師課表", "success")
        return redirect(url_for("timetable_images"))

    if kind == "class":
        combo = (request.form.get("class_combo") or "").strip()
        if combo and "|" in combo:
            grade, class_no = combo.split("|", 1)
        else:
            grade = (request.form.get("grade") or "").strip()
            class_no = (request.form.get("class_no") or "").strip()
        if not grade or not class_no:
            flash("請選擇年級與班級", "warning")
            return redirect(url_for("timetable_images"))
        if not _can_edit_class(current_user, grade, class_no):
            flash("沒有權限上傳此班級課表", "warning")
            return redirect(url_for("timetable_images"))
        for name in saved:
            db.session.add(
                TimetableImage(
                    unit=SCHOOL_NAME,
                    kind="class",
                    file=name,
                    grade=str(grade),
                    class_no=str(class_no),
                    uploader=current_user.username,
                )
            )
        db.session.commit()
        flash(f"已上傳 {len(saved)} 張班級課表", "success")
        return redirect(url_for("timetable_images"))

    flash("未知的上傳類型", "warning")
    return redirect(url_for("timetable_images"))


@app.route("/timetable/images/delete/<int:iid>")
@login_required
def timetable_images_delete(iid):
    img = db.session.get(TimetableImage, iid)
    if not img:
        flash("圖片不存在", "warning")
        return redirect(url_for("timetable_images"))

    
    can = False
    if current_user.role in ("leader", "admin"):
        can = True
    elif current_user.role == "teacher":
        if img.kind == "teacher" and img.owner_username == current_user.username:
            can = True
        elif img.kind == "class" and _can_edit_class(current_user, img.grade, img.class_no):
            can = True

    if not can:
        flash("沒有權限刪除此圖片", "warning")
        return redirect(url_for("timetable_images"))

    
    try:
        os.remove(os.path.join(TIMETABLE_DIR, img.file))
    except Exception:
        pass

    db.session.delete(img)
    db.session.commit()
    flash("已刪除", "success")
    return redirect(url_for("timetable_images"))

@app.route("/profile", methods=["GET","POST"])
@login_required
def profile():
    """
    個人檔案：可編輯基本資料 + 今日所屬學期徽章 + 本學期資訊/行事曆 + 課表圖片（依身份）
    （已解除寬度上限；圖片全寬顯示並可點擊查看）
    """
    msg = ""
    u = current_user
    F = lambda k: (request.form.get(k) or "").strip()

    
    if request.method == "POST":
        display_name = F("display_name")
        bio          = F("bio")
        if not display_name:
            msg = "顯示名稱不可為空。"
        else:
            avatar_error = ""
            remove_avatar = request.form.get("remove_avatar") == "1"
            avatar_file = request.files.get("avatar")
            if remove_avatar:
                u.avatar = None
            if avatar_file and getattr(avatar_file, "filename", "").strip():
                saved_avatar = save_image(avatar_file)
                if saved_avatar:
                    u.avatar = saved_avatar
                else:
                    avatar_error = "頭像格式不支援，請上傳 png、jpg、jpeg 或 gif 圖片。"
            u.display_name = display_name
            u.bio = bio
            if u.role in ("teacher","leader","admin","parent"):
                u.email   = F("email")
                u.phone   = F("phone")
                u.line_id = F("line_id")
                u.office_hours = F("office_hours")
                u.contact_pref = F("contact_pref")
            if u.role in ("teacher", "leader", "admin"):
                u.title = F("title")
                u.subjects = F("subjects")
            if avatar_error:
                msg = avatar_error
            else:
                db.session.commit()
                msg = "個人資料已更新。"

    
    d = today()
    sem, wk = semester_of_date(SCHOOL_NAME, d)
    sem_badge = (
        f"<span class='badge bg-success ms-2'>{sem.name}・第{wk}週</span>"
        if sem else "<span class='badge bg-secondary ms-2'>未在學期內</span>"
    )

    
    style_block = ""

    
    scope_parts = [u.unit, (u.grade and f"{u.grade}年"), (u.class_no and f"{u.class_no}班")]
    scope = " · ".join([x for x in scope_parts if x]) or "—"
    role_label = {
        "student": "學生",
        "parent": "家長",
        "teacher": "教師",
        "leader": "組長",
        "admin": "管理員",
    }.get(u.role, u.role)
    avatar_char = escape(((u.display_name or u.username or "U").strip() or "U")[0])
    avatar_name = _safe_upload_name(getattr(u, "avatar", "") or "")
    if avatar_name:
        avatar_html = (
            "<div class='profile-avatar profile-avatar--image'>"
            f"<img src='{url_for('uploaded_file', filename=avatar_name)}' alt='{escape(u.display_name or u.username)}的頭像'>"
            "</div>"
        )
    else:
        avatar_html = f"<div class='profile-avatar'>{avatar_char}</div>"
    invite_code = parent_invite_code(u.username) if u.role == "student" else ""
    header_html = f"""
    <section class='profile-hero'>
      {avatar_html}
      <div>
        <div class='profile-eyebrow'>個人檔案</div>
        <h2>{escape(u.display_name or u.username)}</h2>
        <div class='profile-meta'>
          <span>{role_label}</span>
          <span>{escape(scope)}</span>
          <span>帳號：{escape(u.username)}</span>
          {sem_badge}
        </div>
      </div>
      <div class='profile-actions'>
        <a class='btn btn-sm btn-outline-success' href='{url_for(role_endpoint(u.role))}'>返回工作區</a>
        <a class='btn btn-sm btn-outline-secondary' href='{url_for('profile_password')}'>變更密碼</a>
        <a class='btn btn-sm btn-outline-primary' href='{url_for('timetable_images')}'>課表管理</a>
      </div>
    </section>
    """

    
    fields_common = (
        "<div class='profile-avatar-editor mb-3'>"
        "<div>"
        "<label class='form-label'>個人頭像</label>"
        "<input type='file' name='avatar' accept='.png,.jpg,.jpeg,.gif' class='form-control'>"
        "<div class='form-text'>可上傳正方形或直式照片，系統會自動裁切成頭像顯示。</div>"
        "</div>"
        f"{('<label class=\"profile-remove-avatar\"><input type=\"checkbox\" name=\"remove_avatar\" value=\"1\"> 移除目前頭像</label>') if avatar_name else ''}"
        "</div>"
        f"<div class='mb-3'><label class='form-label'>顯示名稱</label>"
        f"<input name='display_name' class='form-control' value='{escape(u.display_name or u.username)}' required></div>"
        f"<div class='mb-3'><label class='form-label'>個人簡介</label>"
        f"<textarea name='bio' class='form-control' rows='4' placeholder='關於我'>{escape(getattr(u,'bio','') or '')}</textarea></div>"
    )
    fields_contact = ""
    if u.role in ("teacher","leader","admin","parent"):
        pref_opts = "".join(
            f"<option value='{val}' {'selected' if (getattr(u, 'contact_pref', '') or '') == val else ''}>{label}</option>"
            for val, label in [("", "未指定"), ("email", "Email"), ("phone", "電話"), ("line", "LINE")]
        )
        fields_contact = (
            "<div class='row g-3'>"
            f"<div class='col-md-4'><label class='form-label'>Email</label><input name='email' class='form-control' value='{escape(getattr(u,'email','') or '')}'></div>"
            f"<div class='col-md-4'><label class='form-label'>電話</label><input name='phone' class='form-control' value='{escape(getattr(u,'phone','') or '')}'></div>"
            f"<div class='col-md-4'><label class='form-label'>LINE 帳號</label><input name='line_id' class='form-control' value='{escape(getattr(u,'line_id','') or '')}'></div>"
            f"<div class='col-md-8'><label class='form-label'>可聯絡時段</label><input name='office_hours' class='form-control' value='{escape(getattr(u,'office_hours','') or '')}' placeholder='例：平日 16:00-18:00'></div>"
            f"<div class='col-md-4'><label class='form-label'>偏好聯絡方式</label><select name='contact_pref' class='form-select'>{pref_opts}</select></div>"
            "</div>"
        )
    fields_staff = ""
    if u.role in ("teacher", "leader", "admin"):
        fields_staff = (
            "<div class='row g-3 mt-1'>"
            f"<div class='col-md-4'><label class='form-label'>職稱 / 角色稱謂</label><input name='title' class='form-control' value='{escape(getattr(u,'title','') or '')}' placeholder='例：導師、閱讀教師、組長'></div>"
            f"<div class='col-md-8'><label class='form-label'>負責科目 / 專長</label><input name='subjects' class='form-control' value='{escape(getattr(u,'subjects','') or '')}' placeholder='例：閱讀、國語、自然'></div>"
            "</div>"
        )
    actions = (
        "<div class='d-flex gap-2 mt-4'>"
        "<button class='btn btn-primary'>儲存資料</button>"
        f"<a class='btn btn-outline-secondary' href='{url_for('profile_password')}'>變更密碼</a>"
        "</div>"
    )

    sem_text = f"{sem.name} · 第 {wk} 週" if sem else "未在學期內"
    profile_summary = (
        "<section class='profile-summary-grid'>"
        f"<div class='profile-summary-card'><span>帳號</span><strong>{escape(u.username)}</strong></div>"
        f"<div class='profile-summary-card'><span>身分</span><strong>{role_label}</strong></div>"
        f"<div class='profile-summary-card'><span>所屬範圍</span><strong>{escape(scope)}</strong></div>"
        f"<div class='profile-summary-card'><span>今日學期</span><strong>{escape(sem_text)}</strong></div>"
        "</section>"
    )

    
    form_card = (
        f"{header_html}"
        f"{profile_summary}"
        f"{role_quick_actions_html(u, include_profile=False, extra_class='profile-shortcuts')}"
        "<section class='profile-panel'>"
        "<div class='profile-panel__head'><div><div class='profile-section-label'>資料設定</div><h3>基本資料</h3></div></div>"
        "<form method='post' enctype='multipart/form-data'>"
        f"{fields_common}"
        f"{fields_contact}"
        f"{fields_staff}"
        f"{actions}"
        "</form>"
        "</section>"
    )

    
    sem_block = ""
    if sem:
        cal_imgs = (
            SemesterCalendarImage.query
            .filter_by(semester_id=sem.id)
            .order_by(SemesterCalendarImage.created_at.desc())
            .all()
        )
        cal_grid = (
            "".join(
                
                f"<a class='profile-image-tile' href='{url_for('uploaded_file', filename=im.file)}' target='_blank' rel='noopener'>"
                f"<span>{im.created_at.strftime('%Y-%m-%d')}</span>"
                f"<img src='{url_for('uploaded_file', filename=im.file)}' alt='本學期行事曆'>"
                "</a>"
                for im in cal_imgs
            ) or "<div class='profile-empty'>尚無本學期行事曆圖片</div>"
        )
        sem_block = (
            "<section class='profile-panel'>"
            "<div class='profile-panel__head'>"
            f"<div><div class='profile-section-label'>學期資訊</div><h3>{sem.name}</h3></div>"
            f"<span>{sem.start_date} ~ {sem.end_date} · 第 {wk} 週</span>"
            "</div>"
            "<div class='fw-bold mb-2'>本學期行事曆</div>"
            f"<div class='profile-image-grid'>{cal_grid}</div>"
            "</section>"
        )
    else:
        sem_block = (
            "<section class='profile-panel'>"
            "<div class='text-muted'>目前不在任何學期期間內。</div>"
            "</section>"
        )

    
    blocks = []

    def _img_row(src: str) -> str:
        
        return (
            f"<a class='profile-image-tile' href='{src}' target='_blank' rel='noopener'>"
            "<span>點擊查看完整圖片</span>"
            f"<img src='{src}' alt='課表圖片'>"
            "</a>"
        )

    
    if u.role == "student":
        if invite_code:
            blocks.append(
                "<section class='profile-panel profile-invite-panel'>"
                "<div class='profile-panel__head'>"
                "<div><div class='profile-section-label'>家長綁定</div><h3>家長邀請碼</h3></div>"
                "</div>"
                "<div class='profile-invite-card'>"
                "<div class='profile-invite-actions'>"
                f"<button class='btn btn-success profile-copy-invite' type='button' data-invite-code='{escape(invite_code)}'>複製家長邀請碼</button>"
                "</div>"
                "<div class='profile-invite-hint' id='profileInviteHint'>按下按鈕後即可複製給家長使用。</div>"
                "</div>"
                "</section>"
            )
        else:
            blocks.append(
                "<section class='profile-panel profile-invite-panel'>"
                "<div class='profile-panel__head'><div><div class='profile-section-label'>家長綁定</div><h3>家長邀請碼</h3></div></div>"
                "<div class='profile-empty'>你的學號不是純數字，暫時無法產生家長邀請碼。請聯絡老師或管理員協助。</div>"
                "</section>"
            )
        try:
            today_d = _today()
            ws0, we0 = _week_range(today_d)
            month_first, month_last = _month_range(today_d)
            week_pts_profile, _week_sdg_profile = _user_week_points(u.id, ws0, we0)
            total_pts_profile = 0
            month_pts_profile = 0
            approved_count_profile = 0
            pending_count_profile = 0
            reading_q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(Task.task_type == "mission")
            reading_q = ct_user_filter(reading_q, u.id)
            for ct in reading_q.all():
                status = _ct_status(ct)
                if status == "pending":
                    pending_count_profile += 1
                    continue
                if status != "approved":
                    continue
                approved_count_profile += 1
                payload = _ct_load(ct)
                score = _safe_int(payload.get("score_total", 0), 0)
                total_pts_profile += score
                try:
                    ct_day = _ct_get_timestamp(ct).date()
                    if month_first <= ct_day <= month_last:
                        month_pts_profile += score
                except Exception:
                    pass
            level_profile = _level_of(total_pts_profile)
            week_goal_profile = int(globals().get("READING_WEEK_GOAL", 30) or 0)
            week_pct_profile = max(0, min(100, int(round(week_pts_profile * 100 / week_goal_profile)))) if week_goal_profile > 0 else 0
            week_hint_profile = f"{week_pts_profile} / {week_goal_profile} 分" if week_goal_profile > 0 else f"{week_pts_profile} 分"
            blocks.append(
                "<section class='profile-panel profile-reading-panel'>"
                "<div class='profile-panel__head'>"
                "<div><div class='profile-section-label'>閱讀檔案</div><h3>我的閱讀摘要</h3></div>"
                f"<a class='btn btn-sm btn-success' href='{url_for('homework')}'>前往閱讀專區</a>"
                "</div>"
                "<div class='profile-reading-grid'>"
                f"<div><span>本週分數</span><strong>{week_pts_profile}</strong><em>{escape(week_hint_profile)}</em></div>"
                f"<div><span>本月分數</span><strong>{month_pts_profile}</strong><em>本月已核准閱讀分數</em></div>"
                f"<div><span>學期累積</span><strong>{total_pts_profile}</strong><em>目前等級：{escape(str(level_profile))}</em></div>"
                f"<div><span>待審紀錄</span><strong>{pending_count_profile}</strong><em>已通過 {approved_count_profile} 筆</em></div>"
                "</div>"
                "<div class='profile-reading-progress'>"
                "<div><span>本週目標進度</span><strong>"
                f"{week_pct_profile}%</strong></div>"
                "<div class='progress' style='height:.75rem;'>"
                f"<div class='progress-bar bg-success' style='width:{week_pct_profile}%;' role='progressbar' aria-valuenow='{week_pct_profile}' aria-valuemin='0' aria-valuemax='100'></div>"
                "</div>"
                "</div>"
                "</section>"
            )
        except Exception:
            blocks.append(
                "<section class='profile-panel profile-reading-panel'>"
                "<div class='profile-panel__head'><div><div class='profile-section-label'>閱讀檔案</div><h3>我的閱讀摘要</h3></div></div>"
                "<div class='profile-empty'>目前暫時無法讀取閱讀摘要，稍後再試。</div>"
                "</section>"
            )
        cls_imgs = (
            TimetableImage.query
            .filter_by(unit=SCHOOL_NAME, kind="class", grade=str(u.grade), class_no=str(u.class_no))
            .order_by(TimetableImage.created_at.desc())
            .all()
        )
        grid = (
            "".join(_img_row(url_for('uploaded_file', filename=x.file)) for x in cls_imgs)
            or "<div class='profile-empty'>尚無班級課表圖片</div>"
        )
        blocks.append(
            "<section class='profile-panel'>"
            "<div class='profile-panel__head'><div><div class='profile-section-label'>課表</div><h3>班級課表</h3></div></div>"
            f"<div class='profile-image-grid'>{grid}</div>"
            "</section>"
        )

    
    if u.role == "parent":
        pc = parent_child_query(user=u).first()
        if pc:
            stu = User.query.filter_by(username=pc.student_name).first()
            if stu:
                cls_imgs = (
                    TimetableImage.query
                    .filter_by(unit=SCHOOL_NAME, kind="class", grade=str(stu.grade), class_no=str(stu.class_no))
                    .order_by(TimetableImage.created_at.desc())
                    .all()
                )
                grid = (
                    "".join(_img_row(url_for('uploaded_file', filename=x.file)) for x in cls_imgs)
                    or "<div class='profile-empty'>尚無班級課表圖片</div>"
                )
                blocks.append(
                    "<section class='profile-panel'>"
                    f"<div class='profile-panel__head'><div><div class='profile-section-label'>孩子課表</div><h3>{escape(stu.display_name or stu.username)}的班級課表</h3></div><span>{escape(stu.grade or '')}年{escape(stu.class_no or '')}班</span></div>"
                    f"<div class='profile-image-grid'>{grid}</div>"
                    "</section>"
                )
        else:
            blocks.append(
                "<section class='profile-panel'>"
                "<div class='profile-empty'>尚未綁定孩子，無法顯示班級課表。</div>"
                "</section>"
            )

    
    if u.role in ("teacher","leader","admin"):
        
        my_imgs = (
            TimetableImage.query
            .filter_by(unit=SCHOOL_NAME, kind="teacher", owner_username=u.username)
            .order_by(TimetableImage.created_at.desc())
            .all()
        )
        grid_my = (
            "".join(_img_row(url_for('uploaded_file', filename=x.file)) for x in my_imgs)
            or "<div class='profile-empty'>尚無個人教師課表圖片</div>"
        )
        blocks.append(
            "<section class='profile-panel'>"
            "<div class='profile-panel__head'><div><div class='profile-section-label'>教師課表</div><h3>我的教師課表</h3></div>"
            f"<a class='btn btn-sm btn-outline-primary' href='{url_for('timetable_images')}'>管理/上傳</a>"
            "</div>"
            f"<div class='profile-image-grid'>{grid_my}</div>"
            "</section>"
        )

        
        if is_homeroom_of(u, u.grade, u.class_no):
            cls_imgs = (
                TimetableImage.query
                .filter_by(unit=SCHOOL_NAME, kind="class", grade=str(u.grade), class_no=str(u.class_no))
                .order_by(TimetableImage.created_at.desc())
                .all()
            )
            grid_cls = (
                "".join(_img_row(url_for('uploaded_file', filename=x.file)) for x in cls_imgs)
                or "<div class='profile-empty'>尚無班級課表圖片</div>"
            )
            blocks.append(
                "<section class='profile-panel'>"
                f"<div class='profile-panel__head'><div><div class='profile-section-label'>導師班</div><h3>導師班課表</h3></div><span>{escape(u.grade or '')}年{escape(u.class_no or '')}班</span></div>"
                f"<div class='profile-image-grid'>{grid_cls}</div>"
                "</section>"
            )

        
        scopes = _teacher_scopes(u)
        if scopes:
            inner = []
            for g, c in sorted(scopes):
                imgs = (
                    TimetableImage.query
                    .filter_by(unit=SCHOOL_NAME, kind="class", grade=str(g), class_no=str(c))
                    .order_by(TimetableImage.created_at.desc())
                    .all()
                )
                if imgs:
                    bgrid = "".join(_img_row(url_for('uploaded_file', filename=x.file)) for x in imgs)
                else:
                    bgrid = "<div class='profile-empty'>尚無課表圖片</div>"
                inner.append(
                    f"<div class='profile-subsection'><div class='profile-subsection-title'>{escape(str(g))}年{escape(str(c))}班</div>"
                    f"<div class='profile-image-grid'>{bgrid}</div></div>"
                )
            blocks.append(
                "<section class='profile-panel'>"
                "<div class='profile-panel__head'><div><div class='profile-section-label'>任教班級</div><h3>我任教的班級課表</h3></div></div>"
                + "".join(inner) +
                "</section>"
            )

    
    invite_script = ""
    if u.role == "student" and invite_code:
        invite_script = f"""
        <script>
        (function(){{
          function copyInvite(code){{
            if (!code) return;
            const done = function(){{
              const hint = document.getElementById('profileInviteHint');
              if (hint) {{
                hint.textContent = '已複製家長邀請碼';
                hint.classList.add('is-copied');
              }}
              alert('已複製家長邀請碼');
            }};
            if (navigator.clipboard && navigator.clipboard.writeText) {{
              navigator.clipboard.writeText(code).then(done).catch(function(){{
                fallbackCopy(code);
                done();
              }});
            }} else {{
              fallbackCopy(code);
              done();
            }}
          }}
          function fallbackCopy(code){{
            const area = document.createElement('textarea');
            area.value = code;
            area.setAttribute('readonly', '');
            area.style.position = 'fixed';
            area.style.left = '-9999px';
            document.body.appendChild(area);
            area.select();
            try {{ document.execCommand('copy'); }} catch(e) {{}}
            document.body.removeChild(area);
          }}
          document.querySelectorAll('.profile-copy-invite').forEach(function(btn){{
            btn.addEventListener('click', function(){{
              copyInvite(btn.getAttribute('data-invite-code') || '{escape(invite_code)}');
            }});
          }});
        }})();
        </script>
        """
    content = "<div class='profile-page'>" + form_card + sem_block + "".join(blocks) + invite_script + "</div>"
    return page("個人檔案", style_block + content, msg=msg)

@app.route("/profile/password", methods=["GET","POST"])
@login_required
def profile_password():
    """
    獨立密碼頁
    """
    msg = ""
    F = lambda k: (request.form.get(k) or "").strip()

    if request.method == "POST":
        old  = F("old")
        new1 = F("new1")
        new2 = F("new2")
        if not all([old, new1, new2]):
            msg = "請完整填寫密碼欄位。"
        elif (current_user.password or "") != old:
            msg = "目前密碼不正確。"
        elif new1 != new2:
            msg = "兩次新密碼不一致。"
        else:
            current_user.password = new1
            db.session.commit()
            return toast_redirect("profile", "密碼已更新。", "success")

    form = (
        "<div class='card border-0 shadow-sm mx-auto' style='max-width:600px;'>"
        "<div class='card-body'>"
        "<h5 class='card-title mb-3'>變更密碼</h5>"
        "<form method='post'>"
        "<div class='mb-3'><label class='form-label'>目前密碼</label><input type='password' name='old' class='form-control' required></div>"
        "<div class='mb-3'><label class='form-label'>新密碼</label><input type='password' name='new1' class='form-control' required></div>"
        "<div class='mb-3'><label class='form-label'>確認新密碼</label><input type='password' name='new2' class='form-control' required></div>"
        "<div class='d-flex gap-2'>"
        "<button class='btn btn-warning'>儲存新密碼</button>"
        f"<a class='btn btn-outline-secondary' href='{url_for('profile')}'>返回個人檔案</a>"
        "</div>"
        "</form>"
        "</div></div>"
    )
    return page("變更密碼", form, msg=msg)

@app.route("/parent_sign", methods=["GET", "POST"])
@roles_required("parent")
def parent_sign():
    """
    家長電子簽名：改成『依作業日』簽名，與日記脫鉤。
    /parent_sign?student=<sid>&date=YYYY-MM-DD
    POST: image_data(dataURL), note
    """
    sid = (request.args.get("student") or request.form.get("student") or "").strip()
    
    scope = "homework"
    dsel = parse_date((request.args.get("date") or request.form.get("date") or "")) or local_today()

    
    bound = parent_child_query(student=sid).first()
    stu = User.query.filter_by(username=sid, role="student").first() if bound else None
    if not stu:
        return toast_redirect("parent", "此學童未與你綁定。", "warning")

    
    
    has_hw = False
    try:
        q_hw = Task.query.filter(Task.unit == (stu.unit or ""))
        q_hw = q_hw.filter(
            ((Task.is_school_wide == 1) |
             ((Task.grade == stu.grade) & (Task.class_no == stu.class_no))),
            ((Task.task_type == "homework") | (Task.task_type == None)),   
            ((Task.start_date == None) | (Task.start_date <= dsel)),       
            ((Task.end_date == None) | (Task.end_date >= dsel)),           
        )
        has_hw = q_hw.first() is not None
    except Exception:
        has_hw = False

    
    has_diary = False
    try:
        has_diary = (
            DiaryPrompt.query.filter_by(
                unit=stu.unit,
                grade=stu.grade,
                class_no=stu.class_no,
                date=dsel,
            )
            .order_by(DiaryPrompt.id.desc())
            .first()
            is not None
        )
    except Exception:
        has_diary = False

    if not (has_hw or has_diary):
        
        return toast_redirect(
            "parent",
            f"{dsel} 沒有老師布置的作業或日記，本日無需家長簽名。",
            "info",
            date=dsel.isoformat()
        )

    
    if request.method == "POST":
        data_url = request.form.get("image_data")
        note = (request.form.get("note") or "").strip()
        saved = save_dataurl_png(data_url)
        if not saved:
            return page("家長簽名", _sign_form_html(sid, scope, dsel, msg="上傳失敗，請重新簽名。"))

        
        abs_path = os.path.join(app.config["UPLOAD_FOLDER"], saved)
        watermark = f"{dsel} · 學童：{stu.display_name or stu.username}"
        add_watermark_text(abs_path, watermark)

        
        exist = signature_of(current_user.username, sid, dsel, scope)
        if exist:
            exist.image = saved
            exist.note = note or None
            exist.created_at = datetime.now()
        else:
            db.session.add(
                ParentSignature(
                    parent_name=current_user.username,
                    student_name=sid,
                    date=dsel,
                    scope=scope,
                    image=saved,
                    note=note or None,
                )
            )
        db.session.commit()

        
        return toast_redirect("parent", "已送出家長簽名。", "success", date=dsel.isoformat())

    
    return page("家長簽名", _sign_form_html(sid, scope, dsel))

@app.route("/invite")
@roles_required("student")
def invite():
    code = parent_invite_code(current_user.username)
    if not code:
        tip = "<div class='alert alert-warning'>你的學號不是純數字，無法產生家長邀請碼。請聯絡老師或管理員。</div>"
        return page("家長邀請碼", tip)

    html = f"""
    <div class='text-center' style='max-width:520px;margin:0 auto;'>
      <div class='card border-0 shadow-sm mb-3 card--glow'>
        <div class='card-body'>
          <div class='h5 mb-3'>家長邀請碼</div>
          <input class='form-control invite-code-field text-center' id='parentInviteCode' value='{code}' readonly aria-label='家長邀請碼'>
          <div class='text-muted small mt-2'>家長在註冊頁輸入此碼即可完成綁定</div>
          <div class='d-grid gap-2 mt-3'>
            <button class='btn btn-primary' id='copyBtn' data-ripple>複製邀請碼</button>
            <a class='btn btn-outline-secondary' href='/register'>前往家長註冊頁</a>
          </div>
        </div>
      </div>
	      <p class='small text-muted'>請將此邀請碼提供給家長完成綁定。</p>
    </div>
    <script>
    (function(){{
      const btn = document.getElementById('copyBtn');
      btn?.addEventListener('click', function(){{
        const codeField = document.getElementById('parentInviteCode');
        navigator.clipboard.writeText(codeField ? codeField.value : "{code}").then(function(){{
          alert("已複製家長邀請碼");
        }});
      }});
    }})();
    </script>
    """
    return page("家長邀請碼", html)

@app.route("/diary_write/<int:pid>", methods=["GET","POST"])
@roles_required("student")
def diary_write(pid):
    """
    學生：針對某題目寫/改日記，可附圖，並決定是否公開給家長
    過期鎖定：若 app.config['LOCK_PAST_DATES']=True（預設 True），題目日期已過則不可新增或修改
    """
    from flask import current_app

    p = DiaryPrompt.query.get_or_404(pid)

    
    if not (p.unit == current_user.unit and p.grade == current_user.grade and p.class_no == current_user.class_no):
        return toast_redirect("diary", "你不是此班學生，無法繳交此題目。", "warning")

    
    lock_on = current_app.config.get("LOCK_PAST_DATES", True)
    is_locked = bool(lock_on and p.date and today() > p.date)

    sub = DiarySubmission.query.filter_by(prompt_id=pid, student_name=current_user.username).first()

    
    if is_locked:
        msg = "此日記已過期，無法新增或修改。" if not sub else "此日記已過期，已無法再修改。"
        return toast_redirect("diary", msg, "warning")

    if request.method == "POST":
        content = g("content")
        share = 1 if (request.form.get("share_to_parent") in ("1", "on", "true", "True")) else 0

        saved = None
        fs = request.files.get("image")
        if fs and fs.filename.strip():
            saved = save_image(fs)  

        if not content:
            
            return page(
                "寫日記",
                _diary_form_html(p, sub, content or "", share, saved or (sub.image if sub else None)),
                msg="內容不可為空。"
            )

        if sub:
            sub.content = content
            sub.share_to_parent = share
            if saved:
                sub.image = saved
        else:
            db.session.add(DiarySubmission(
                prompt_id=pid, student_name=current_user.username,
                content=content, image=saved, share_to_parent=share
            ))
        db.session.commit()
        target_date = (p.date or today()).isoformat()
        return toast_redirect("homework", "日記已儲存。", "success", date=target_date)

    
    return page(
        "寫日記",
        _diary_form_html(
            p, sub,
            (sub.content if sub else ""),
            (sub.share_to_parent if sub else 0),
            (sub.image if sub else None)
        )
    )


def _diary_form_html(p: "DiaryPrompt", sub: "DiarySubmission|None", content: str, share: int, image_name: str | None):
    img_block = (
        "<div class='mb-3'><label class='form-label'>已上傳圖片（可重傳覆蓋）</label>"
        f"{img_html(image_name, maxw=320)}</div>"
    ) if image_name else ""
    prompt_img_block = img_html(diary_prompt_img_name(p), maxw=300)

    fb_block = ""
    if sub and getattr(sub, "teacher_comment", None):
        who = display_name_of(getattr(sub, "teacher_username", "") or "") or "老師"
        when = sub.teacher_commented_at or ""
        fb_block = (
            "<div class='mt-4 p-3 border rounded bg-light'>"
            "<div class='fw-bold mb-1'>老師回饋</div>"
            f"<div style='white-space:pre-wrap;'>{sub.teacher_comment}</div>"
            f"<div class='small text-muted mt-1'>{who} · {when}</div>"
            "</div>"
        )

    return (
        "<div class='mb-3'>"
        f"<div class='h5 mb-1'>{p.date}｜{p.title}</div>"
        f"{('<div class=\"text-muted\">' + _user_text_html(p.instruction) + '</div>') if p.instruction else ''}"
        f"{prompt_img_block}"
        "</div>"
        "<form method='post' enctype='multipart/form-data' style='max-width:720px;'>"
        "<div class='mb-3'><label class='form-label'>日記內容（必填）</label>"
        f"<textarea name='content' class='form-control' rows='6' placeholder='請完整寫下今天的想法、觀察或心得。' required>{content or ''}</textarea></div>"
        f"{img_block}"
        "<div class='mb-3'><label class='form-label'>附加圖片（選填，最多 1 張，最大 4MB）</label>"
        "<input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"
        "<div class='setting-group mb-3'>"
        "<div class='setting-group__label'>公開設定</div>"
        f"{setting_checkbox_html('share_to_parent', 'share_to_parent', '開放家長閱讀這篇日記', '家長可閱讀。', checked=bool(share))}"
        "</div>"
        "<button class='btn btn-primary'>儲存</button> "
        "<a class='btn btn-outline-secondary' href='/diary'>返回清單</a>"
        "</form>"
        f"{fb_block}"
    )

@app.route("/diary")
@login_required
def diary():
    level = (request.args.get("toast_level") or "info").strip()
    msg   = (request.args.get("toast_msg") or "").strip()
    if msg:
        try:
            flash(msg, level)  
        except Exception:
            pass

    dsel = parse_date((request.args.get("date") or "").strip()) or today()
    role = getattr(current_user, "role", "")

    if role == "student":
        pro = (DiaryPrompt.query
               .filter_by(unit=current_user.unit, grade=current_user.grade,
                          class_no=current_user.class_no, date=dsel)
               .order_by(DiaryPrompt.id.desc()).first())
        if pro:
            
            return redirect(url_for("diary_write", pid=pro.id))
        return redirect(url_for("homework", date=dsel.isoformat()))

    if role == "teacher":
        try:
            return redirect(url_for("teacher_diary"))
        except Exception:
            return redirect(url_for("homework", date=dsel.isoformat()))

    if role == "parent":
        try:
            return redirect(url_for("parent", date=dsel.isoformat()) + "#parent-diary-panel")
        except Exception:
            return redirect(url_for("homework", date=dsel.isoformat()))

    return redirect(url_for("homework", date=dsel.isoformat()))







from datetime import datetime, date, timedelta
from collections import defaultdict
import json, os, uuid
from sqlalchemy import func  


if "today" not in globals():
    def today() -> date:
        return date.today()

if "parse_date" not in globals():
    def parse_date(s: str | None):
        try:
            return date.fromisoformat((s or "").strip())
        except Exception:
            return None


if "kv_prefix" not in globals():
    def kv_prefix(desc: str, settings: dict) -> str:
        try:
            return f"KV:{json.dumps(settings, ensure_ascii=False)}\n{desc or ''}"
        except Exception:
            pairs = ";".join([f"{k}={v}" for k, v in settings.items()])
            return f"KV:{pairs}\n{desc or ''}"

if "kv_parse" not in globals():
    def kv_parse(desc: str) -> dict:
        s = (desc or "")
        if not s.startswith("KV:"):
            return {}
        nl = s.find("\n")
        head = s[3:nl] if nl != -1 else s[3:]
        try:
            return json.loads(head)
        except Exception:
            out = {}
            for part in head.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    out[k.strip()] = v.strip()
            return out

if "toast_redirect" not in globals():
    def toast_redirect(endpoint: str, msg: str = "", level: str = "info"):
        return redirect(url_for(endpoint))  

if "page" not in globals():
    def page(title: str, html: str, msg: str = ""):
        tpl = f"""
        <div class='container py-3'>
          <h4 class='mb-3'>{title}</h4>
          {("<div class='alert alert-warning'>" + msg + "</div>") if msg else ""}
          {html}
        </div>
        """
        return render_template_string(tpl)  

if "display_name_of" not in globals():
    def display_name_of(username: str) -> str:
        try:
            u = User.query.filter_by(username=username).first()  
            return (u.display_name or u.username) if u else (username or "")
        except Exception:
            return username or ""


SDG_OPTIONS = [
    (1, "無貧窮"), (2, "零飢餓"), (3, "良好健康"), (4, "優質教育"),
    (5, "性別平等"), (6, "淨水與衛生"), (7, "可負擔潔淨能源"),
    (8, "合適的工作與經濟成長"), (9, "產業創新與基礎建設"),
    (10, "減少不平等"), (11, "永續城鄉"), (12, "責任消費與生產"),
    (13, "氣候行動"), (14, "海洋生態"), (15, "陸域生態"),
    (16, "和平正義與制度"), (17, "夥伴關係"),
]
WEEK_BADGES = [30, 45, 60]  
LEVELS = [200, 500]         

def _sdg_name(code: int) -> str:
    for c, name in SDG_OPTIONS:
        if c == code:
            return name
    return f"SDG {code}"


def _now():
    return datetime.now()

def _today():
    try:
        return today()  
    except Exception:
        return date.today()

def _week_range(d: date):
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end

def _month_range(d: date):
    first = d.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1, day=1)
    else:
        nxt = first.replace(month=first.month + 1, day=1)
    last = nxt - timedelta(days=1)
    return first, last

def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def _user_scope():
    return (
        getattr(current_user, "unit", None),
        getattr(current_user, "grade", None),
        getattr(current_user, "class_no", None),
    )

def _same_school_class(u1, u2):
    return (
        getattr(u1, "unit", None),
        getattr(u1, "grade", None),
        getattr(u1, "class_no", None),
    ) == (
        getattr(u2, "unit", None),
        getattr(u2, "grade", None),
        getattr(u2, "class_no", None),
    )


def _save_images_or_fallback(file_list, subdir="read"):
    
    try:
        return _save_images(file_list, subdir=subdir)  
    except Exception:
        pass

    names = []
    up_dir = app.config.get("UPLOAD_FOLDER") or UPLOAD_DIR
    os.makedirs(up_dir, exist_ok=True)

    for f in file_list:
        if not getattr(f, "filename", ""):
            continue
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "dat"
        fname = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(up_dir, fname))
        names.append(fname)
    return names


def _ct_storage_slot():
    slots = ["payload_json", "payload", "description", "content", "body", "reflection"]
    for s in slots:
        if hasattr(CompletedTask, s):  
            return s
    return None

def _json_loads(s):
    if not s:
        return {}
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except Exception:
        return {}

def _ct_load(ct: "CompletedTask") -> dict:  
    slot = _ct_storage_slot()
    raw = getattr(ct, slot, None) if slot else None
    data = _json_loads(raw)

    
    if "status" not in data:
        ap = getattr(ct, "approved", None)
        if ap is None:
            data["status"] = "pending"
        elif ap == 1:
            data["status"] = "approved"
        else:
            data["status"] = "rejected"
            rr = getattr(ct, "reject_reason", None)
            if rr:
                data["reject_reason"] = rr

    
    if not data.get("reflection_text") and hasattr(ct, "reflection"):
        if isinstance(ct.reflection, str) and ct.reflection.strip():
            j = _json_loads(ct.reflection)
            if j:
                for k, v in j.items():
                    data.setdefault(k, v)
            else:
                data["reflection_text"] = ct.reflection

    
    if not data.get("photos") and hasattr(ct, "proof_image") and getattr(ct, "proof_image", None):
        data["photos"] = [ct.proof_image]

    if "parent_coread" not in data and (data.get("parent_reflection") or "").strip():
        data["parent_coread"] = True

    return data

def reading_need_parent_coread(ct=None, payload: dict | None = None, task=None) -> bool:
    payload = payload or {}
    need = False

    if ct is not None and hasattr(ct, "with_parent"):
        try:
            need = bool(getattr(ct, "with_parent") or 0)
        except Exception:
            need = False

    if not need and bool(payload.get("need_parent_coread")):
        need = True

    if not need and task is None and ct is not None:
        task = getattr(ct, "task", None)
        if task is None and getattr(ct, "task_id", None):
            try:
                task = Task.query.get(getattr(ct, "task_id", None))
            except Exception:
                task = None

    if not need and task is not None:
        try:
            need = task_meta(task).get(META_PARENT) == "1"
        except Exception:
            need = False

    return bool(need)

def reading_parent_coread_done(payload: dict | None) -> bool:
    payload = payload or {}
    return bool((payload.get("parent_reflection") or "").strip())

def _ct_save(ct: "CompletedTask", data: dict) -> None:  
    slot = _ct_storage_slot()
    dump = json.dumps(data, ensure_ascii=False)
    if slot:
        setattr(ct, slot, dump)
    else:
        ct.reflection = dump

    st = data.get("status")
    if st == "approved":
        ct.approved = 1
        if hasattr(ct, "reject_reason"):
            ct.reject_reason = None
        if hasattr(ct, "approved_by") and data.get("approved_by"):
            ct.approved_by = data["approved_by"]
        if hasattr(ct, "approved_at"):
            v = data.get("approved_at")
            if isinstance(v, str):
                try:
                    ct.approved_at = datetime.fromisoformat(v)
                except Exception:
                    ct.approved_at = datetime.utcnow()
            elif isinstance(v, datetime):
                ct.approved_at = v
            else:
                ct.approved_at = datetime.utcnow()
    elif st == "rejected":
        ct.approved = 0
        if hasattr(ct, "reject_reason"):
            ct.reject_reason = data.get("reject_reason") or "未說明"
    else:
        ct.approved = None

def _ct_status(ct: "CompletedTask") -> str:  
    p = _ct_load(ct)
    st = (p.get("status") or "").strip().lower()
    if st in ("pending", "approved", "rejected"):
        return st
    ap = getattr(ct, "approved", None)
    return "pending" if ap is None else ("approved" if ap == 1 else "rejected")

def _ct_user(ct: "CompletedTask"):  
    if hasattr(ct, "student_id") and getattr(ct, "student_id", None):
        try:
            return User.query.get(int(ct.student_id))  
        except Exception:
            pass
    name = getattr(ct, "student_name", None)
    if name:
        u = User.query.filter_by(username=name).first()  
        if u:
            return u
        try:
            return User.query.get(int(name))  
        except Exception:
            pass
    return None

def ct_user_filter(q, user_id: int):
    if hasattr(CompletedTask, "student_id"):  
        return q.filter(CompletedTask.student_id == user_id)  
    u = User.query.get(user_id)  
    uname = getattr(u, "username", None) if u else None
    return q.filter(CompletedTask.student_name == uname) if uname else q

def _ct_set_user(ct: "CompletedTask", user: "User") -> None:  
    if hasattr(ct, "student_id"):
        ct.student_id = getattr(user, "id", None)
    if hasattr(ct, "student_name"):
        ct.student_name = getattr(user, "username", None) or str(getattr(user, "id", ""))

def _ct_time_set(ct: "CompletedTask", dt: datetime) -> None:  
    if hasattr(ct, "created_at") and not getattr(ct, "created_at", None):
        ct.created_at = dt

def _ct_get_timestamp(ct: "CompletedTask") -> datetime:  
    for n in ("created_at", "approved_at"):
        if hasattr(ct, n):
            v = getattr(ct, n)
            if isinstance(v, datetime):
                return v
    
    p = _ct_load(ct)
    if p.get("timestamp_iso"):
        try:
            return datetime.fromisoformat(p["timestamp_iso"])
        except Exception:
            pass
    return datetime.utcnow()

def ct_time_col():
    if hasattr(CompletedTask, "created_at"):  
        return CompletedTask.created_at  
    if hasattr(CompletedTask, "approved_at"):  
        return CompletedTask.approved_at  
    return None


def _user_week_points(user_id: int, week_start: date, week_end: date):
    """回傳：(本週總分, 本週 SDG set)。僅計入 status == approved。"""
    q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(  
        Task.task_type == "mission"  
    )
    q = ct_user_filter(q, user_id)
    col = ct_time_col()
    if col is not None:
        q = q.filter(
            func.date(col) >= week_start.isoformat(),
            func.date(col) <= week_end.isoformat(),
        )
        recs = q.all()
    else:
        recs = ct_user_filter(CompletedTask.query, user_id).all()  

    total = 0
    sdg_set = set()
    for ct in recs:
        ts = _ct_get_timestamp(ct)
        if ts.date() < week_start or ts.date() > week_end:
            if col is None:
                continue
        payload = _ct_load(ct)
        if payload.get("status") != "approved":
            continue
        total += _safe_int(payload.get("score_total", 0), 0)
        for s in payload.get("sdg_codes", []):
            n = _safe_int(s, 0)
            if n > 0:
                sdg_set.add(n)
    return total, sdg_set

def _streak_weeks(user_id: int, up_to: date):
    cnt = 0
    cur_start, cur_end = _week_range(up_to)
    while True:
        w_points, _ = _user_week_points(user_id, cur_start, cur_end)
        if w_points > 0:
            cnt += 1
            prev_end = cur_start - timedelta(days=1)
            cur_start, cur_end = _week_range(prev_end)
        else:
            break
    return cnt

def _month_cont_bonus(user_id: int, reference_day: date):
    """本月連續週加成上限 6 分（每有一週有閱讀就 +2，最多 3 週）"""
    first, last = _month_range(reference_day)
    day = first
    reached = 0
    seen_weeks = []
    while day <= last:
        ws, we = _week_range(day)
        if (ws, we) not in seen_weeks:
            pts, _ = _user_week_points(user_id, ws, we)
            if pts > 0:
                reached += 2
            seen_weeks.append((ws, we))
        day += timedelta(days=7 - day.weekday())
    return min(reached, 6)

def _cooldown_same_book_block(user_id: int, isbn: str, title: str, new_ts: datetime):
    """7 天內同一本書不再給「完成 +10」的分數。"""
    seven = new_ts - timedelta(days=7)
    q = ct_user_filter(CompletedTask.query, user_id).order_by(  
        (ct_time_col() or CompletedTask.id).desc()  
    )
    for ct in q:
        payload = _ct_load(ct)
        if payload.get("status") != "approved":
            continue
        prev_ts = _ct_get_timestamp(ct)
        if prev_ts < seven:
            break
        if (isbn and payload.get("isbn") == isbn) or (
            title and payload.get("book_title", "").strip() == (title or "").strip()
        ):
            return True
    return False

def _abnormal_fast_flag(user_id: int, new_ts: datetime):
    """10 分鐘內連續 3 筆以上，標記為「異常速投」供老師參考。"""
    ten = new_ts - timedelta(minutes=10)
    q = ct_user_filter(CompletedTask.query, user_id)  
    col = ct_time_col()
    if col is not None:
        return q.filter(col >= ten).count() >= 3
    recs = q.all()
    recent = [ct for ct in recs if _ct_get_timestamp(ct) >= ten]
    return len(recent) >= 3

def _compute_score_preview(user, reflection_tag, sdg_codes, title, isbn, when_dt: datetime):
    
    
    
    
    complete = 10
    if _cooldown_same_book_block(user.id, isbn, title, when_dt):
        complete = 0

    ref_map = {"none": 0, "some": 3, "great": 5}
    reflection_pts = ref_map.get(reflection_tag, 0)

    base_total = complete + reflection_pts

    ws, we = _week_range(when_dt.date())
    _, cur_sdg = _user_week_points(user.id, ws, we)
    combined_sdg = set(cur_sdg) | {_safe_int(x, 0) for x in (sdg_codes or []) if _safe_int(x, 0) > 0}
    diversity_bonus = 3 if len([x for x in combined_sdg if x]) >= 2 else 0

    streak = _streak_weeks(user.id, when_dt.date())
    cont_bonus_should = 2 if streak >= 1 else 0
    already = _month_cont_bonus(user.id, when_dt.date())
    cont_bonus = 0 if already >= 6 else (min(2, 6 - already) if cont_bonus_should else 0)

    total = base_total + diversity_bonus + cont_bonus
    return {
        "score_total": total,
        "breakdown": {
            "complete": complete,
            "reflection": reflection_pts,
            "diversity_week": diversity_bonus,
            "continuity_week": cont_bonus,
        },
    }

def _next_badge_delta(user_id: int, ref_day: date):
    ws, we = _week_range(ref_day)
    pts, _ = _user_week_points(user_id, ws, we)
    for th in WEEK_BADGES:
        if pts < th:
            return th - pts, th, pts
    return 0, WEEK_BADGES[-1], pts

def _level_of(total_points):
    if total_points < LEVELS[0]:
        return "綠芽"
    if total_points < LEVELS[1]:
        return "小樹"
    return "大樹"

def _month_sdg_scores(user_id: int, reference_day: date):
    """
    本月各 SDG 類別累計分數（只算已核准）。
    一次認證的總分平均分配到該次選取的所有 SDG。
    """
    first, last = _month_range(reference_day)
    col = ct_time_col()
    q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(  
        Task.task_type == "mission"  
    )
    q = ct_user_filter(q, user_id)
    if col is not None:
        q = q.filter(
            func.date(col) >= first.isoformat(),
            func.date(col) <= last.isoformat(),
        )
        recs = q.all()
    else:
        recs = ct_user_filter(CompletedTask.query, user_id).all()  

    scores = {code: 0.0 for code, _ in SDG_OPTIONS}
    for ct in recs:
        p = _ct_load(ct)
        if p.get("status") != "approved":
            continue
        sc = _safe_int(p.get("score_total", 0), 0)
        codes = [_safe_int(c, 0) for c in p.get("sdg_codes", []) if _safe_int(c, 0) > 0]
        if not codes or sc <= 0:
            continue
        share = sc / len(codes)
        for c in codes:
            if c in scores:
                scores[c] += share
    return scores


def _sdg_label(code: int | str | None) -> str:
    c = _safe_int(code, 0)
    if c <= 0:
        return "學生自行選擇"
    return f"SDG {c:02d} {_sdg_name(c)}"


def _reading_chart_colors() -> list[str]:
    return [
        "#34784a", "#78b95a", "#e7a63a", "#4f8f61", "#5aa6a1", "#7d8cc4",
        "#d16f54", "#9bbf43", "#2f6f5e", "#c08f2f", "#5f9f73", "#7aa7d9",
        "#b7749a", "#9a7a52", "#6c9d3f", "#d6b84d", "#56846d",
    ]


def _reading_pie_path(cx: float, cy: float, r: float, start_deg: float, end_deg: float) -> str:
    def point(angle: float) -> tuple[float, float]:
        rad = math.radians(angle - 90)
        return cx + r * math.cos(rad), cy + r * math.sin(rad)

    x1, y1 = point(start_deg)
    x2, y2 = point(end_deg)
    large_arc = 1 if (end_deg - start_deg) > 180 else 0
    return (
        f"M {cx:.2f} {cy:.2f} "
        f"L {x1:.2f} {y1:.2f} "
        f"A {r:.2f} {r:.2f} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z"
    )


def _reading_sdg_chart_html(sdg_scores: dict, sdg_names: dict | None = None, empty_text: str = "尚無圖表資料。") -> str:
    sdg_names = sdg_names or {code: name for code, name in SDG_OPTIONS}
    active = []
    for raw_code, raw_score in (sdg_scores or {}).items():
        code = _safe_int(raw_code, 0)
        score = _safe_int(raw_score, 0)
        if code > 0 and score > 0:
            active.append((code, str(sdg_names.get(code, _sdg_name(code))), score))

    if not active:
        return (
            "<div class='reading-chart-grid'>"
            "<div class='reading-chart-card reading-chart-card--pie'>"
            "<div class='reading-chart-head'><span>圓餅圖</span><small>本月 SDG 比例</small></div>"
            "<div class='reading-chart-pie-wrap'>"
            "<svg class='reading-chart-pie reading-chart-pie--empty' viewBox='0 0 200 200' role='img' aria-label='尚無 SDG 圓餅圖資料'>"
            "<circle cx='100' cy='100' r='78' fill='#e8f2ea'></circle>"
            "<circle cx='100' cy='100' r='38' fill='#ffffff' opacity='.94'></circle>"
            "<text x='100' y='101' text-anchor='middle' class='reading-chart-pie-label'>尚無資料</text>"
            "</svg>"
            "<div class='reading-chart-empty'>"
            f"{escape(empty_text)}"
            "</div></div></div>"
            "<div class='reading-chart-card'>"
            "<div class='reading-chart-head'><span>長條圖</span><small>本月各 SDG 分數</small></div>"
            "<div class='reading-chart-bars reading-chart-bars--empty'>"
            "<div class='reading-chart-bar-row'><div class='reading-chart-bar-name'>SDG 主題</div><div class='reading-chart-bar-track'><div class='reading-chart-bar-fill' style='width:38%;background:#dbe9df;'></div></div><div class='reading-chart-bar-score'>0 分</div></div>"
            "<div class='reading-chart-bar-row'><div class='reading-chart-bar-name'>閱讀累積</div><div class='reading-chart-bar-track'><div class='reading-chart-bar-fill' style='width:24%;background:#e5efd8;'></div></div><div class='reading-chart-bar-score'>0 分</div></div>"
            "<div class='reading-chart-bar-row'><div class='reading-chart-bar-name'>等待通過</div><div class='reading-chart-bar-track'><div class='reading-chart-bar-fill' style='width:16%;background:#eef3ed;'></div></div><div class='reading-chart-bar-score'>0 分</div></div>"
            "</div></div></div>"
        )

    active.sort(key=lambda item: item[2], reverse=True)
    total = sum(score for _code, _name, score in active)
    colors = _reading_chart_colors()

    pie_items = active[:6]
    other_total = sum(score for _code, _name, score in active[6:])
    if other_total:
        pie_items.append((0, "其他 SDG", other_total))

    start = 0.0
    slices = []
    legend_items = []
    for idx, (code, name, score) in enumerate(pie_items):
        color = colors[idx % len(colors)]
        angle = 360.0 * score / total if total else 0
        end = start + angle
        label = f"SDG {code:02d} {name}" if code else name
        pct_txt = round(score * 100 / total) if total else 0
        if angle >= 359.5:
            slices.append(f"<circle cx='100' cy='100' r='78' fill='{color}'></circle>")
        else:
            slices.append(f"<path d='{_reading_pie_path(100, 100, 78, start, end)}' fill='{color}'></path>")
        legend_items.append(
            "<div class='reading-chart-legend-item'>"
            f"<span class='reading-chart-dot' style='background:{color};'></span>"
            f"<span>{escape(label)}</span>"
            f"<strong>{score} 分 · {pct_txt}%</strong>"
            "</div>"
        )
        start = end

    max_score = max(score for _code, _name, score in active) if active else 1
    bar_rows = []
    for idx, (code, name, score) in enumerate(active[:8]):
        color = colors[idx % len(colors)]
        width = max(6, round(score * 100 / max_score)) if max_score else 0
        bar_rows.append(
            "<div class='reading-chart-bar-row'>"
            f"<div class='reading-chart-bar-name'>SDG {code:02d} {escape(name)}</div>"
            "<div class='reading-chart-bar-track'>"
            f"<div class='reading-chart-bar-fill' style='width:{width}%;background:{color};'></div>"
            "</div>"
            f"<div class='reading-chart-bar-score'>{score} 分</div>"
            "</div>"
        )

    return (
        "<div class='reading-chart-grid'>"
        "<div class='reading-chart-card reading-chart-card--pie'>"
        "<div class='reading-chart-head'><span>圓餅圖</span><small>本月 SDG 比例</small></div>"
        "<div class='reading-chart-pie-wrap'>"
        "<svg class='reading-chart-pie' viewBox='0 0 200 200' role='img' aria-label='SDG 圓餅圖'>"
        + "".join(slices)
        + "<circle cx='100' cy='100' r='38' fill='#ffffff' opacity='.94'></circle>"
        + f"<text x='100' y='96' text-anchor='middle' class='reading-chart-pie-total'>{total}</text>"
        + "<text x='100' y='116' text-anchor='middle' class='reading-chart-pie-label'>分</text>"
        + "</svg>"
        "<div class='reading-chart-legend'>"
        + "".join(legend_items)
        + "</div></div></div>"
        "<div class='reading-chart-card'>"
        "<div class='reading-chart-head'><span>長條圖</span><small>本月各 SDG 分數</small></div>"
        "<div class='reading-chart-bars'>"
        + "".join(bar_rows)
        + "</div></div></div>"
    )


def _reading_class_options(user) -> list[tuple[str, str, str, str]]:
    classes: list[tuple[str, str]] = []
    if getattr(user, "role", None) in ("leader", "admin"):
        rows = (
            User.query.filter_by(unit=getattr(user, "unit", SCHOOL_NAME), role="student")
            .order_by(User.grade, User.class_no)
            .all()
        )
        classes = [(str(s.grade), str(s.class_no)) for s in rows if s.grade and s.class_no]
        if not classes:
            classes = [(g, c) for g in GRADE_OPTIONS for c in CLASS_OPTIONS]
    else:
        if is_homeroom_of(user, getattr(user, "grade", ""), getattr(user, "class_no", "")):
            classes.append((str(user.grade), str(user.class_no)))
        for subj, grade, class_no in teacher_assignments(getattr(user, "username", "")):
            if str(subj).strip() == READING_SUBJECT:
                classes.append((str(grade), str(class_no)))

    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for grade, class_no in classes:
        key = (str(grade), str(class_no))
        if key in seen or not all(key):
            continue
        seen.add(key)
        out.append((f"{key[0]}|{key[1]}", f"{key[0]}年{key[1]}班", key[0], key[1]))
    out.sort(key=lambda x: (_safe_int(x[2], 999), _safe_int(x[3], 999)))
    return out


def _task_applies_to_student(task: "Task", user: "User") -> bool:
    if getattr(task, "is_school_wide", 0):
        return task.unit == user.unit
    if task.grade and not task.class_no:
        return task.unit == user.unit and str(task.grade) == str(user.grade)
    return (
        task.unit == user.unit
        and str(task.grade or "") == str(user.grade or "")
        and str(task.class_no or "") == str(user.class_no or "")
    )


def _assigned_reading_tasks_for_student(user: "User") -> list["Task"]:
    today_d = _today()
    tasks = (
        Task.query.filter(Task.task_type == "mission", Task.unit == user.unit)
        .order_by(Task.end_date.asc(), Task.id.desc())
        .all()
    )
    visible = []
    for task in tasks:
        if not is_assigned_task(task) or not _task_applies_to_student(task, user):
            continue
        if task.start_date and today_d < task.start_date:
            continue
        visible.append(task)
    return visible


def _latest_submission_for_task(task_id: int, student_name: str):
    return (
        CompletedTask.query.filter_by(task_id=task_id, student_name=student_name)
        .order_by(CompletedTask.id.desc())
        .first()
    )


@app.route("/reading/tasks", methods=["GET", "POST"])
@roles_required("teacher", "leader", "admin")
def reading_task_manage():
    class_options = _reading_class_options(current_user)
    class_map = {value: (grade, class_no) for value, _label, grade, class_no in class_options}
    msg = ""

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        target = (request.form.get("target_class") or "").strip()
        sdg_code = _safe_int(request.form.get("sdg_code"), 0)
        source = (request.form.get("reading_source") or "").strip()
        description = (request.form.get("description") or "").strip()
        points = max(10, _safe_int(request.form.get("points"), 10))
        end_date = parse_date(request.form.get("end_date")) or _today()
        need_parent = bool(request.form.get("need_parent_coread"))

        if not title:
            msg = "請輸入任務名稱。"
        elif target not in class_map:
            msg = "請選擇可發布的班級。"
        elif not can_manage_reading_class(current_user, *class_map[target]):
            msg = "只有該班班導或閱讀授課教師可以發布閱讀任務。"
        elif not description:
            msg = "請填寫任務說明。"
        else:
            grade, class_no = class_map[target]
            meta = {
                META_MODE: MODE_ASSIGNED,
                META_SDG_CODE: sdg_code if sdg_code else "",
                META_SOURCE: source,
                META_PARENT: "1" if need_parent else "0",
            }
            task = Task(
                title=title,
                description=build_description(description, meta),
                created_by=current_user.username,
                category=None,
                mission_category="永續閱讀任務",
                points=points,
                start_date=_today(),
                end_date=end_date,
                unit=getattr(current_user, "unit", None) or SCHOOL_NAME,
                grade=grade,
                class_no=class_no,
                is_school_wide=0,
                task_type="mission",
                is_view_only=0,
            )
            db.session.add(task)
            db.session.commit()
            return toast_redirect("reading_task_manage", "已建立永續閱讀任務。", "success")

    today_iso = _today().isoformat()
    class_opts_html = (
        "".join(f"<option value='{value}'>{label}</option>" for value, label, _g, _c in class_options)
        or "<option value=''>目前沒有可發布的閱讀班級</option>"
    )
    create_disabled = "disabled" if not class_options else ""
    permission_hint = (
        "<div class='reading-task-notice'>"
        "閱讀任務目前只開放該班班導，或已被指派為「閱讀」科目的授課教師發布。"
        "</div>"
    )
    sdg_opts_html = "<option value=''>學生提交時自行選擇</option>" + "".join(
        f"<option value='{code}'>{code:02d} {name}</option>" for code, name in SDG_OPTIONS
    )

    tasks_all = (
        Task.query.filter(Task.task_type == "mission", Task.unit == (getattr(current_user, "unit", None) or SCHOOL_NAME))
        .order_by(Task.id.desc())
        .all()
    )
    task_cards = []
    assigned_count = 0
    active_count = 0
    parent_required_count = 0
    pending_total = 0
    approved_total = 0
    for task in tasks_all:
        if not is_assigned_task(task):
            continue
        if current_user.role == "teacher" and not can_manage_reading_class(current_user, task.grade, task.class_no):
            continue
        subs = CompletedTask.query.filter_by(task_id=task.id).all()
        pending = sum(1 for sub in subs if _ct_status(sub) == "pending")
        approved = sum(1 for sub in subs if _ct_status(sub) == "approved")
        meta = task_meta(task)
        assigned_count += 1
        pending_total += pending
        approved_total += approved
        if not task.end_date or task.end_date >= _today():
            active_count += 1
        if meta.get(META_PARENT) == "1":
            parent_required_count += 1
        parent_badge = "<span class='reading-task-badge reading-task-badge--parent'>親子共讀</span>" if meta.get(META_PARENT) == "1" else ""
        sdg_text = _sdg_label(meta.get(META_SDG_CODE))
        source_text = (meta.get(META_SOURCE) or "").strip()
        source_line = f"<div class='reading-task-source'>閱讀來源：{escape(source_text)}</div>" if source_text else ""
        is_active = not task.end_date or task.end_date >= _today()
        status_label = "進行中" if is_active else "已截止"
        status_class = "reading-task-status--active" if is_active else "reading-task-status--ended"
        review_url = url_for("reading_review_queue", status="pending" if pending else "all")
        review_total = approved + pending
        progress_pct = int(round((approved / review_total) * 100)) if review_total else 0
        progress_label = "尚未收到提交" if review_total == 0 else f"{progress_pct}% 已通過"
        task_cards.append(
            "<article class='reading-task-item'>"
            "<div class='reading-task-item__main'>"
            "<div class='reading-task-item__top'>"
            "<div>"
            f"<div class='reading-task-name'>{escape(task.title)} {parent_badge}</div>"
            f"<div class='reading-task-desc'>{escape(task_body(task))}</div>"
            f"{source_line}"
            "</div>"
            f"<span class='reading-task-status {status_class}'>{status_label}</span>"
            "</div>"
            "<div class='reading-task-meta-grid'>"
            f"<div><span>班級範圍</span><strong>{scope_txt(task)}</strong></div>"
            f"<div><span>SDG 主題</span><strong>{sdg_text}</strong></div>"
            f"<div><span>截止日</span><strong>{task.end_date or '未設定'}</strong></div>"
            f"<div><span>積分基準</span><strong>{task.points or 10} 分</strong></div>"
            "</div>"
            "</div>"
            "<div class='reading-task-item__side'>"
            "<div class='reading-task-counts'>"
            f"<div><strong>{approved}</strong><span>已通過</span></div>"
            f"<div><strong>{pending}</strong><span>待審核</span></div>"
            "</div>"
            "<div class='reading-task-progress'>"
            f"<div><span>審核進度</span><strong>{progress_label}</strong></div>"
            f"<div class='reading-task-progress-bar'><i style='width:{progress_pct}%;'></i></div>"
            "</div>"
            "<div class='reading-task-actions'>"
            f"<a class='btn btn-sm btn-success' href='{review_url}'>查看審核</a>"
            f"<a class='btn btn-sm btn-outline-danger' href='/delete_task/{task.id}' onclick='return confirm(\"刪除此永續閱讀任務？\");'>刪除</a>"
            "</div>"
            "</div>"
            "</article>"
        )

    back_url = url_for(role_endpoint(current_user.role))
    review_queue_url = url_for("reading_review_queue", status="pending")

    form_html = f"""
    <div class='reading-task-page'>
    <section class='reading-task-hero'>
      <div>
        <div class='reading-submit-kicker'>Reading Mission</div>
        <h3>永續閱讀任務管理</h3>
        <p>以班級為單位發布閱讀任務，整合學生閱讀提交、老師審核與親子共讀回饋，讓閱讀推動從指派、繳交到回饋都能被清楚追蹤。</p>
      </div>
      <div class='reading-task-hero-actions'>
        <a class='btn btn-success reading-task-back' href='{review_queue_url}'>前往閱讀審核</a>
        <a class='btn btn-outline-secondary reading-task-back' href='{back_url}'>返回工作區</a>
      </div>
    </section>
    <section class='reading-task-workflow'>
      <div><span>01</span><strong>發布任務</strong><small>設定班級、SDG、截止日與任務說明。</small></div>
      <div><span>02</span><strong>學生提交</strong><small>學生於閱讀專區繳交書名、心得與佐證。</small></div>
      <div><span>03</span><strong>老師審核</strong><small>通過後累積閱讀分數，退回則留下回饋。</small></div>
    </section>
    <div class='reading-task-summary'>
      <div><span>任務總數</span><strong>{assigned_count}</strong><small>已建立的指定閱讀</small></div>
      <div><span>進行中</span><strong>{active_count}</strong><small>仍在截止日前</small></div>
      <div><span>親子共讀</span><strong>{parent_required_count}</strong><small>需要家長回饋</small></div>
      <div><span>待審核</span><strong>{pending_total}</strong><small>已通過 {approved_total}</small></div>
    </div>
    <div class='card border-0 shadow-sm mb-4 reading-task-card'>
      <div class='card-body'>
        <div class='reading-task-card-head'>
          <div>
            <h5 class='card-title mb-1'>建立永續閱讀任務</h5>
            <div class='small text-muted'>先選班級與閱讀主題，再設定截止日、積分與是否需要親子共讀。</div>
          </div>
        </div>
        {permission_hint}
        {("<div class='alert alert-warning'>" + msg + "</div>") if msg else ""}
        <form method='post' class='reading-task-form reading-task-form--single'>
          <div class='reading-task-field reading-task-field--wide'>
            <label class='form-label'>任務名稱</label>
            <input name='title' class='form-control' placeholder='例：氣候行動主題閱讀' required>
          </div>
          <div class='reading-task-field'>
            <label class='form-label'>發布班級</label>
            <select name='target_class' class='form-select' required>{class_opts_html}</select>
          </div>
          <div class='reading-task-field'>
            <label class='form-label'>截止日</label>
            <input type='date' name='end_date' class='form-control' value='{today_iso}' required>
          </div>
          <div class='reading-task-field reading-task-field--wide'>
            <label class='form-label'>指定 SDG 主題</label>
            <select name='sdg_code' class='form-select'>{sdg_opts_html}</select>
          </div>
          <div class='reading-task-field'>
            <label class='form-label'>任務積分基準</label>
            <input type='number' name='points' class='form-control' value='10' min='10' step='1'>
          </div>
          <div class='reading-task-field'>
            <label class='form-label'>閱讀來源</label>
            <input name='reading_source' class='form-control' placeholder='自由選書、指定書名或文章連結'>
          </div>
          <div class='reading-task-field reading-task-field--full'>
            <label class='form-label'>任務說明</label>
            <textarea name='description' class='form-control' rows='3' placeholder='請說明閱讀主題、完成方式與注意事項。' required></textarea>
          </div>
          <div class='reading-task-field reading-task-field--full'>
            <div class='setting-group'>
              <div class='setting-group__label'>延伸設定</div>
              {setting_checkbox_html('need_parent_coread', 'need_parent_coread', '指定為親子共讀任務', '家長需補寫共讀心得，照片可作為佐證。')}
            </div>
          </div>
          <div class='reading-task-form-actions'>
            <button class='btn btn-primary' {create_disabled}>建立任務</button>
          </div>
        </form>
      </div>
    </div>
    """

    empty_task_card = (
        "<div class='reading-task-empty'>"
        "<strong>目前尚未建立永續閱讀任務</strong>"
        "<span>建立任務後，學生會在閱讀專區看到指定閱讀，老師可於閱讀審核區追蹤繳交狀態。</span>"
        "</div>"
    )
    table_html = (
        "<div class='card border-0 shadow-sm reading-task-card'><div class='card-body'>"
        "<div class='reading-task-card-head'><div><h5 class='card-title mb-1'>已建立的永續閱讀任務</h5>"
        "<div class='small text-muted'>以卡片呈現任務狀態、班級範圍與審核進度，方便快速管理。</div></div></div>"
        f"<div class='reading-task-list'>{''.join(task_cards) or empty_task_card}</div>"
        "</div></div></div>"
    )

    return page("永續閱讀任務管理", form_html + table_html)

@app.route("/reading/submit/<int:task_id>", methods=["GET", "POST"])
@login_required
def reading_submit(task_id):
    from sqlalchemy.exc import IntegrityError
    from flask import current_app, url_for
    from sqlalchemy import func
    from datetime import datetime

    
    SDG17 = [
        (1, "無貧窮"), (2, "零飢餓"), (3, "健康與福祉"), (4, "優質教育"), (5, "性別平等"),
        (6, "淨水與衛生"), (7, "可負擔及潔淨能源"), (8, "合適工作與經濟成長"),
        (9, "產業創新與基礎建設"), (10, "減少不平等"), (11, "永續城鄉"),
        (12, "責任消費與生產"), (13, "氣候行動"), (14, "海洋生態"),
        (15, "陸域生態"), (16, "和平正義與健全制度"), (17, "夥伴關係"),
    ]
    SDG17_CODES = {code for code, _ in SDG17}

    task = Task.query.get_or_404(task_id)
    task_is_assigned = is_assigned_task(task)
    rmeta = task_meta(task)
    assigned_sdg = _safe_int(rmeta.get(META_SDG_CODE), 0)
    parent_required = (rmeta.get(META_PARENT) == "1")
    clean_task_description = task_body(task)

    
    if getattr(current_user, "role", "") != "student":
        return toast_redirect("home", "僅限學生帳號可提交。", "warning")  

    
    if getattr(task, "task_type", "") != "mission":
        return toast_redirect("home", "此任務非閱讀任務。", "warning")  

    
    in_scope = bool(
        task.is_school_wide
        or (
            task.unit == current_user.unit and
            task.grade == current_user.grade and
            task.class_no == current_user.class_no
        )
    )
    if not in_scope:
        return toast_redirect("home", "你不在此任務適用範圍。", "danger")  

    
    if task.start_date and _today() < task.start_date:
        return toast_redirect("home", "尚未到發布日期，暫不可提交。", "warning")  
    lock_on = bool(current_app.config.get("LOCK_PAST_DATES", True))
    if lock_on and task.end_date and _today() > task.end_date:
        return toast_redirect("home", "此任務已截止，無法再提交。", "warning")  

    
    stu_name = (getattr(current_user, "username", None) or getattr(current_user, "id", None))
    if not stu_name:
        return toast_redirect("home", "登入資訊異常，請重新登入後再試。", "warning")  
    stu_name = str(stu_name)

    g = request.form.get
    msg = ""

    
    existing_q = CompletedTask.query.filter_by(task_id=task.id, student_name=stu_name)
    existing_all = existing_q.order_by(CompletedTask.id.desc()).all()
    submitted_count = len(existing_all)
    blocking_submissions = [ct for ct in existing_all if _ct_status(ct) in ("pending", "approved")]
    MAX_ENTRIES = 1 if task_is_assigned else 10

    
    def _status_badge(st: str) -> str:
        if st == "approved":
            return "<span class='badge bg-success'>已通過</span>"
        if st == "rejected":
            return "<span class='badge bg-danger'>已退回</span>"
        if st == "withdrawn":
            return "<span class='badge bg-secondary'>已撤回</span>"
        return "<span class='badge bg-warning text-dark'>待審核</span>"

    def _sdg_name(code: int) -> str:
        return next((n for c, n in SDG17 if c == code), "—")

    def _need_parent_coread(payload: dict, ct=None) -> bool:
        return reading_need_parent_coread(ct=ct, payload=payload, task=task)

    
    if request.method == "POST":
        if task_is_assigned and blocking_submissions:
            msg = "這項老師指定任務已經送出或通過；若被退回，才需要重新提交。"
        elif (not task_is_assigned) and submitted_count >= MAX_ENTRIES:
            msg = f"本週閱讀任務每位同學最多認證 {MAX_ENTRIES} 次，你已達上限。"
        else:
            book_title = (g("book_title") or "").strip()
            reflection = (g("reflection") or "").strip()
            sdg_code = assigned_sdg or _safe_int(g("sdg_code") or 0, 0)
            photos = request.files.getlist("photos")
            want_parent_coread = bool(parent_required)

            
            if not book_title:
                msg = "請填寫書名。"
            elif sdg_code not in SDG17_CODES:
                msg = "請選擇一個 SDG 類別。"
            elif not reflection:
                msg = "閱讀認證需要一小段閱讀心得，請寫一點點你的想法。"

            if not msg:
                img_names = _save_images_or_fallback(photos, subdir="read")

                payload = {
                    "status": "pending",
                    "task_id": task.id,
                    "reading_mode": MODE_ASSIGNED if task_is_assigned else MODE_AUTONOMOUS,
                    "reading_source_label": source_label(task),
                    "reading_task_title": task.title,
                    "reading_source": rmeta.get(META_SOURCE, ""),
                    "reading_requirement": "",
                    "book_title": book_title,
                    "sdg_code": sdg_code,
                    "sdg_codes": [sdg_code],
                    "reflection_text": reflection,
                    "reflection_tag": "some",
                    "photos": img_names,
                    "timestamp_iso": _now().isoformat(),

                    
                    "need_parent_coread": bool(want_parent_coread),
                    "parent_coread": False,
                }

                ct = CompletedTask(task_id=task.id)  
                ct.student_name = stu_name
                if hasattr(ct, "with_parent"):
                    ct.with_parent = 1 if want_parent_coread else 0

                ct.has_reflection = 1 if reflection else 0
                ct.has_image = 1 if img_names else 0
                ct.proof_image = img_names[0] if img_names else None

                try:
                    _ct_set_user(ct, current_user)
                except Exception:
                    pass
                try:
                    _ct_time_set(ct, _now())
                except Exception:
                    ct.created_at = datetime.utcnow()

                _ct_save(ct, payload)

                db.session.add(ct)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    return toast_redirect(
                        "reading_submit",
                        "提交失敗，請重新登入後再試。",
                        "danger",
                        task_id=task.id,
                    )  

                
                if want_parent_coread:
                    return toast_redirect(
                        "reading_submit",
                        "已送出閱讀認證（親子共讀）。請提醒家長登入補寫共讀心得；照片可作佐證，完成後老師即可審核。",
                        "success",
                        task_id=task.id,
                    )  
                else:
                    return toast_redirect("reading_submit", "已送出閱讀認證，等待老師審核。", "success", task_id=task.id)  

    
    if assigned_sdg:
        sdg_select_html = (
            f"<input type='hidden' name='sdg_code' value='{assigned_sdg}'>"
            f"<div class='form-control bg-light'>{_sdg_label(assigned_sdg)}</div>"
        )
    else:
        sdg_select_html = (
            "<select name='sdg_code' class='form-select' required>"
            "<option value=''>請選擇一個 SDG 類別</option>"
            + "".join(f"<option value='{code}'>{code:02d} {name}</option>" for code, name in SDG17)
            + "</select>"
        )

    
    today_d = _today()
    ws, we = _week_range(today_d)
    week_pts, _week_sdg = _user_week_points(current_user.id, ws, we)
    remaining = 0 if (task_is_assigned and blocking_submissions) else max(0, MAX_ENTRIES - submitted_count)
    task_mode_badge = (
        "<span class='badge bg-primary'>老師指定任務</span>"
        if task_is_assigned else
        "<span class='badge bg-success'>自主閱讀</span>"
    )
    source_text = escape(rmeta.get(META_SOURCE, "") or "未指定")
    clean_desc_html = _desc_clean_html(clean_task_description or task.description or "")
    limit_hint = (
        "老師指定任務每位同學完成一次提交即可。若被退回，修改後可再次送出。"
        if task_is_assigned else
        "自主閱讀每週最多可送出 10 次，通過後計入分數。"
    )
    task_intro_html = f"""
    <section class='reading-submit-task'>
      <div class='d-flex justify-content-between align-items-start flex-wrap gap-2'>
        <div>
          <div class='reading-submit-kicker'>Reading Task</div>
          <div class='reading-submit-task-title'>{escape(task.title)} {task_mode_badge}</div>
          <div class='reading-submit-task-desc'>{clean_desc_html or '完成閱讀後，請填寫書名、SDG 類別與心得。'}</div>
        </div>
        <div class='reading-submit-task-meta'>
          <span>截止：{task.end_date or '不限'}</span>
          <strong>{_sdg_label(assigned_sdg)}</strong>
        </div>
      </div>
      {f"<div class='reading-submit-requirement'><span>閱讀來源</span><strong>{source_text}</strong></div>" if task_is_assigned else ""}
    </section>
    """
    if parent_required:
        parent_setting_html = (
            "<input type='hidden' name='want_parent_coread' value='1'>"
            "<div class='setting-group'>"
            "<div class='setting-group__label'>親子共讀設定</div>"
            "<div class='setting-check' style='cursor:default;'>"
            "<span class='setting-check__copy'>"
        "<span class='setting-check__title'>此任務指定為親子共讀</span>"
        "<span class='setting-check__hint'>學生送出後，家長會在家長工作頁看到待補寫項目；完成家長回饋後，老師即可審核。</span>"
            "</span></div></div>"
        )
    elif task_is_assigned:
        parent_setting_html = (
            "<div class='setting-group'>"
            "<div class='setting-group__label'>親子共讀設定</div>"
            "<div class='small text-muted'>一般閱讀任務</div>"
            "</div>"
        )
    else:
        parent_setting_html = ""
    parent_setting_col = f"<div class='col-md-6'>{parent_setting_html}</div>" if parent_setting_html else ""
    submit_disabled = " disabled" if (task_is_assigned and blocking_submissions) else ""
    submit_label = "已送出任務" if submit_disabled else ("送出永續閱讀任務" if task_is_assigned else "送出閱讀認證")
    step_parent = (
        "<article><strong>4</strong><span>家長補寫親子共讀</span></article>"
        if parent_required else
        "<article><strong>4</strong><span>等待老師審核</span></article>"
    )
    submit_steps = (
        "<div class='reading-submit-steps'>"
        "<article><strong>1</strong><span>填寫書名</span></article>"
        "<article><strong>2</strong><span>選擇 SDG 主題</span></article>"
        "<article><strong>3</strong><span>寫心得與上傳佐證</span></article>"
        f"{step_parent}"
        "</div>"
    )

    summary_cards = f"""
    <div class='reading-submit-stats'>
      <div>
        <span>本週閱讀分數</span>
        <strong>{week_pts}</strong>
        <small>{ws.strftime("%m/%d")} - {we.strftime("%m/%d")}</small>
      </div>
      <div>
        <span>本週已送出</span>
        <strong>{submitted_count} 次</strong>
        <small>上限 {MAX_ENTRIES} 次</small>
      </div>
      <div>
        <span>剩餘可送出</span>
        <strong>{remaining} 次</strong>
        <small>{limit_hint}</small>
      </div>
    </div>

    """
    back_homework_url = url_for("homework")

    
    rows = []
    show_n = 8

    for ct in existing_all[:show_n]:
        p = _ct_load(ct) or {}
        st = (p.get("status") or "pending")
        try:
            ts = _ct_get_timestamp(ct).strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts = "—"

        code = _safe_int(p.get("sdg_code") or (p.get("sdg_codes") or [0])[0], 0)
        sdg_txt = f"{code:02d} {_sdg_name(code)}" if code in SDG17_CODES else "—"

        if st == "approved":
            score_val = _safe_int(p.get("score_total", 0), 0)
            score_txt = f"{score_val} 分"
        else:
            score_txt = "尚未給分"

        
        need_coread = _need_parent_coread(p, ct=ct)
        coread_badge = "<span class='badge bg-light text-muted border'>親子共讀</span>" if need_coread else ""

        
        action_html = "<span class='text-muted small'>—</span>"
        if st == "pending":
            action_html = f"""
            <form method='post' action='{url_for("reading_withdraw", ct_id=ct.id)}'
                  onsubmit="return confirm('確定要撤回這筆尚未審核的閱讀認證嗎？撤回後不會送到老師審核。');">
              <button class='btn btn-sm btn-outline-danger'>撤回</button>
            </form>
            """

        rows.append(
            "<tr>"
            f"<td class='text-nowrap small text-muted'>{ts}</td>"
            f"<td class='small'>{(p.get('book_title') or '—')[:18]}</td>"
            f"<td class='text-nowrap small'>{sdg_txt}</td>"
            f"<td class='text-nowrap small'>{score_txt}</td>"
            f"<td class='text-nowrap'>{_status_badge(st)} {coread_badge}</td>"
            f"<td class='text-end'>{action_html}</td>"
            "</tr>"
        )

    history_html = f"""
    <div class='card border-0 shadow-sm mt-3'>
      <div class='card-body'>
        <div class='d-flex justify-content-between align-items-center mb-2'>
          <div class='fw-semibold'>我的閱讀認證紀錄（最近 {show_n} 筆）</div>
          <div class='small text-muted'>待審核可撤回</div>
        </div>
        <div class='table-responsive'>
          <table class='table table-sm align-middle mb-0'>
            <thead>
              <tr class='small text-muted'>
                <th style='width:9rem'>時間</th>
                <th style='width:9rem'>書名</th>
                <th style='width:11rem'>SDG 類別</th>
                <th style='width:7rem'>老師給分</th>
                <th style='width:11rem'>狀態</th>
                <th style='width:7rem' class='text-end'>操作</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows) or '<tr><td colspan="6" class="text-muted text-center">尚無認證紀錄，從第一篇心得開始吧！</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """

    
    form_html = f"""
    <div class='reading-submit-page'>
    <div class='card border-0 shadow-sm reading-submit-card'>
      <div class='card-body'>
        <div class='reading-submit-head'>
          <div>
	            <div class='reading-submit-kicker'>Submit</div>
	            <h5 class='card-title mb-1'>{'提交永續閱讀任務' if task_is_assigned else '提交閱讀認證'}</h5>
	            <div class='small text-muted'>來源：{source_label(task)} · {limit_hint}</div>
          </div>
          <div class='d-flex gap-2'>
            <a class='btn btn-sm btn-outline-secondary' href='{back_homework_url}'>回作業頁面</a>
          </div>
        </div>

        <hr class='my-3'>

	        {task_intro_html}
	        {submit_steps}
	        {summary_cards}
        {("<div class='alert alert-warning mb-2'>" + msg + "</div>") if msg else ""}

        <form method='post' enctype='multipart/form-data' class='row g-3 reading-submit-form'>
          <div class='col-12'>
            <div class='reading-submit-section-title'>閱讀內容</div>
          </div>
          <div class='col-md-6'>
            <label class='form-label'>書名</label>
            <input name='book_title' class='form-control' placeholder='例如：小王子、昆蟲記…' required>
          </div>

          <div class='col-md-6'>
            <label class='form-label'>SDG 類別（下拉選擇一項）</label>
            {sdg_select_html}
          </div>

          <div class='col-12'>
            <label class='form-label'>閱讀心得（必填）</label>
            <textarea name='reflection' rows='4' class='form-control'
                      placeholder='寫下你從這本書學到什麼、感受到什麼，至少幾句話。' required></textarea>
          </div>

          <div class='col-12'>
            <div class='reading-submit-section-title'>佐證與親子共讀</div>
          </div>
          <div class='col-md-6'>
            <label class='form-label'>照片證明（可多張，可留空）</label>
            <input type='file' name='photos' class='form-control' accept='.png,.jpg,.jpeg,.gif' multiple>
            <div class='form-text small'>例如：閱讀情境、書本封面、重點筆記等。</div>
          </div>

          {parent_setting_col}

          <div class='col-12 d-flex gap-2'>
	            <button class='btn btn-success' type='submit'{submit_disabled}>{submit_label}</button>
            <a class='btn btn-outline-secondary' href='{back_homework_url}'>回作業頁面</a>
          </div>
        </form>
      </div>
    </div>
    </div>
    """

    head = f"<div class='small text-muted mb-2'>{task.unit or ''} · {task.grade or ''}年{task.class_no or ''}班</div>"
    return page("提交閱讀認證", head + form_html + history_html)  

@app.route("/reading/parent_coread/<int:ct_id>", methods=["GET", "POST"])
@roles_required("parent")
def reading_parent_coread(ct_id):
    ct = CompletedTask.query.get_or_404(ct_id)
    payload = _ct_load(ct) or {}
    task = getattr(ct, "task", None) or Task.query.get(getattr(ct, "task_id", None))
    stu = User.query.filter_by(username=getattr(ct, "student_name", ""), role="student").first()
    if not stu:
        return toast_redirect("parent", "找不到這位學童，請確認資料是否仍存在。", "warning")
    if not parent_child_query(student=stu.username).first():
        return toast_redirect("parent", "此學童未與你綁定，無法填寫親子共讀。", "warning")

    if not reading_need_parent_coread(ct=ct, payload=payload, task=task):
        return toast_redirect(
            "parent",
            "這筆閱讀紀錄不是老師指定的親子共讀任務。",
            "info",
        )

    status = _ct_status(ct)
    if status == "rejected":
        return toast_redirect(
            "parent",
            "這筆閱讀認證已退回，請先等待孩子修正後再填寫。",
            "warning",
        )

    msg = ""
    current_reflection = (payload.get("parent_reflection") or "").strip()
    current_photo = (payload.get("parent_photo") or "").strip()

    if request.method == "POST":
        reflection = (request.form.get("parent_reflection") or "").strip()
        photo_file = request.files.get("parent_photo")
        new_photo = ""
        if photo_file and getattr(photo_file, "filename", ""):
            names = _save_images_or_fallback([photo_file], subdir="read")
            new_photo = names[0] if names else ""

        if not reflection:
            msg = "請寫下家長共讀回饋，讓老師看見陪伴歷程。"
        else:
            payload["need_parent_coread"] = True
            payload["parent_coread"] = True
            payload["parent_reflection"] = reflection
            payload["parent_photo"] = new_photo or current_photo
            payload["parent_updated_by"] = current_user.username
            payload["parent_updated_at"] = _now().isoformat()
            if hasattr(ct, "with_parent"):
                ct.with_parent = 1

            if _ct_status(ct) == "approved":
                breakdown = payload.get("score_breakdown") or {}
                old_bonus = _safe_int(breakdown.get("parent_coread", 0), 0)
                if old_bonus < 10:
                    breakdown["parent_coread"] = 10
                    payload["score_breakdown"] = breakdown
                    payload["score_total"] = _safe_int(payload.get("score_total", 0), 0) + (10 - old_bonus)

            _ct_save(ct, payload)
            db.session.add(ct)
            db.session.commit()
            return toast_redirect(
                "parent",
                "親子共讀已儲存，老師可以看到家長回饋了。",
                "success",
            )

    book_title = escape((payload.get("book_title") or "未填書名").strip())
    task_title = escape(getattr(task, "title", "") or payload.get("reading_task_title", "") or "永續閱讀任務")
    task_desc = _desc_clean_html(task_body(task) if task else payload.get("reading_requirement", ""))
    stu_name = escape(stu.display_name or stu.username)
    stu_info = escape(f"{stu.unit or ''} · {stu.grade or ''}年{stu.class_no or ''}班")
    ts_txt = _ct_get_timestamp(ct).strftime("%Y-%m-%d %H:%M")
    student_reflection = _user_text_html((payload.get("reflection_text") or "孩子尚未留下心得內容。").strip())
    parent_ref_value = escape(current_reflection)
    dashboard_url = url_for("parent") + "#parentReadingOverview"

    student_photos = ""
    for nm in (payload.get("photos") or [])[:4]:
        student_photos += img_html(str(nm), maxw=220)
    if not student_photos:
        student_photos = "<div class='coread-muted'>孩子這次沒有上傳閱讀照片。</div>"

    if current_photo:
        parent_photo_preview = (
            "<div class='coread-current-photo'>"
            "<div class='coread-mini-label'>目前家長照片</div>"
            f"{img_html(current_photo, maxw=220)}"
            "</div>"
        )
        photo_hint = "若不更換照片，可保留空白。"
    else:
        parent_photo_preview = ""
        photo_hint = "照片可留空；若有共讀照片或書本紀錄，可上傳給老師參考。"

    done = reading_parent_coread_done(payload)
    submit_text = "更新親子共讀" if done else "送出親子共讀"
    status_chip = "<span class='badge bg-success'>家長已完成</span>" if done else "<span class='badge bg-warning text-dark'>待家長補寫</span>"
    if status == "approved":
        review_chip = "<span class='badge bg-success'>老師已通過</span>"
    elif done:
        review_chip = "<span class='badge bg-warning text-dark'>等待老師審核</span>"
    else:
        review_chip = "<span class='badge bg-light text-muted border'>尚未進入審核</span>"
    parent_step_class = "coread-step coread-step--done" if done else "coread-step coread-step--active"
    teacher_step_class = "coread-step coread-step--done" if status == "approved" else "coread-step"
    flow_note = (
        "家長心得已完成，老師端會出現在閱讀審核清單中。"
        if done else
        "補寫家長心得後，這筆閱讀才會進入老師可審核清單。"
    )
    parent_photo_state = "已附家長照片" if current_photo else "照片可選填"
    child_photo_count = len(payload.get("photos") or [])
    child_photo_state = f"{child_photo_count} 張孩子照片" if child_photo_count else "孩子未附照片"
    parent_hint = "可直接修改後重新儲存。" if done else "請補寫一段家長共讀回饋。"
    coread_summary_html = f"""
        <div class='coread-summary-grid'>
          <div>
            <span>閱讀任務</span>
            <strong>{task_title}</strong>
            <small>{_sdg_label((payload.get('sdg_code') or 0))}</small>
          </div>
          <div>
            <span>孩子提交</span>
            <strong>{book_title}</strong>
            <small>{child_photo_state}</small>
          </div>
          <div>
            <span>家長回饋</span>
            <strong>{'已完成' if done else '待補寫'}</strong>
            <small>{parent_photo_state}</small>
          </div>
        </div>
    """
    coread_helper_html = f"""
        <div class='coread-helper-card'>
          <div>
            <div class='coread-mini-label'>填寫方向</div>
            <strong>{parent_hint}</strong>
          </div>
          <div class='coread-helper-list'>
            <span>孩子讀了什麼</span>
            <span>共讀時聊到什麼</span>
            <span>想給孩子的鼓勵</span>
          </div>
        </div>
    """
    msg_html = f"<div class='alert alert-warning'>{msg}</div>" if msg else ""

    style = """
    <style>
    .coread-page{display:flex;flex-direction:column;gap:1.1rem;}
    .coread-hero{position:relative;overflow:hidden;border-radius:30px;padding:1.45rem;background:linear-gradient(135deg,#ecfdf3 0%,#fff7dd 58%,#eef7ff 100%);border:1px solid rgba(35,92,59,.1);box-shadow:0 24px 60px rgba(24,76,46,.12);}
    .coread-hero:before{content:"";position:absolute;right:-5rem;top:-5rem;width:17rem;height:17rem;border-radius:50%;background:rgba(89,167,113,.16);}
    .coread-hero-inner{position:relative;display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;flex-wrap:wrap;}
    .coread-kicker{font-size:.78rem;font-weight:900;letter-spacing:.1em;text-transform:uppercase;color:#4f8f61;margin-bottom:.25rem;}
    .coread-hero h3,.coread-panel h4{margin:0;color:#162318;font-weight:900;letter-spacing:-.02em;}
    .coread-muted{color:#647067;font-size:.94rem;}
    .coread-grid{display:grid;grid-template-columns:1fr;gap:1rem;align-items:start;}
    .coread-panel{background:rgba(255,255,255,.97);border:1px solid rgba(19,56,35,.08);border-radius:26px;box-shadow:0 20px 48px rgba(24,76,46,.1);overflow:hidden;}
    .coread-panel-head{padding:1.05rem 1.15rem;border-bottom:1px solid rgba(19,56,35,.08);background:linear-gradient(180deg,#fff 0%,#fbfdf8 100%);}
    .coread-panel-body{padding:1.15rem;}
    .coread-step-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem;margin-top:1rem;}
    .coread-step{border-radius:18px;background:rgba(255,255,255,.86);border:1px solid rgba(35,92,59,.1);padding:.85rem;font-weight:850;color:#244c32;}
    .coread-step--active{background:#fff8e7;border-color:rgba(209,142,36,.28);color:#7a4b12;}
    .coread-step--done{background:#ecfdf3;border-color:rgba(52,120,74,.18);color:#245c36;}
    .coread-step span{display:block;color:#718078;font-size:.8rem;font-weight:750;margin-top:.15rem;}
    .coread-summary-grid{position:relative;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.75rem;margin-top:.85rem;}
    .coread-summary-grid>div{border-radius:18px;background:rgba(255,255,255,.82);border:1px solid rgba(35,92,59,.1);padding:.85rem .95rem;min-width:0;}
    .coread-summary-grid span{display:block;color:#718078;font-size:.78rem;font-weight:900;letter-spacing:.05em;}
    .coread-summary-grid strong{display:block;color:#183d28;font-size:1.05rem;font-weight:950;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:.18rem;}
    .coread-summary-grid small{display:block;color:#647067;font-size:.82rem;font-weight:800;margin-top:.15rem;}
    .coread-flow-note{position:relative;margin-top:.9rem;border-radius:18px;background:rgba(255,255,255,.78);border:1px solid rgba(35,92,59,.1);padding:.85rem 1rem;color:#4d5d51;font-weight:800;}
    .coread-book{border-radius:20px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;margin-bottom:.85rem;}
    .coread-book-title{font-weight:900;color:#17231b;font-size:1.05rem;}
    .coread-reflection{border-radius:18px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:1rem;line-height:1.75;color:#243027;}
    .coread-photo-list{display:flex;gap:.75rem;flex-wrap:wrap;margin-top:.8rem;}
    .coread-photo-list .attachment-preview,.coread-current-photo .attachment-preview{margin:0;}
    .coread-photo-list .attachment-preview__link,.coread-current-photo .attachment-preview__link{max-width:16rem;}
    .coread-form-card{border-radius:22px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .coread-form-card textarea,.coread-form-card .form-control{border-radius:16px;border-color:rgba(19,56,35,.14);}
    .coread-form-card textarea{font-size:1.02rem;line-height:1.75;}
    .coread-helper-card{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;border-radius:20px;background:linear-gradient(135deg,#f0fdf4 0%,#fffdf7 100%);border:1px solid rgba(35,92,59,.1);padding:.95rem 1rem;margin-bottom:.9rem;}
    .coread-helper-card strong{display:block;color:#183d28;font-weight:950;}
    .coread-helper-list{display:flex;gap:.45rem;flex-wrap:wrap;justify-content:flex-end;}
    .coread-helper-list span{display:inline-flex;border-radius:999px;background:#fff;border:1px solid rgba(35,92,59,.11);color:#2f7446;font-size:.82rem;font-weight:850;padding:.32rem .62rem;}
    .coread-current-photo{border-radius:18px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:.85rem;margin-bottom:.9rem;}
    .coread-mini-label{font-size:.8rem;font-weight:900;color:#4f8f61;margin-bottom:.4rem;}
    .coread-actions{display:flex;gap:.55rem;flex-wrap:wrap;align-items:center;margin-top:1rem;}
    .coread-btn{border-radius:999px!important;font-weight:850!important;padding:.55rem 1rem!important;}
    @media (max-width:991.98px){.coread-step-grid,.coread-summary-grid{grid-template-columns:1fr;}.coread-hero,.coread-panel{border-radius:22px;}.coread-helper-list{justify-content:flex-start;}}
    </style>
    """

    content = f"""
    {style}
    <div class='coread-page'>
      <section class='coread-hero'>
        <div class='coread-hero-inner'>
          <div>
            <div class='coread-kicker'>親子共讀</div>
            <h3>親子共讀回饋</h3>
            <div class='coread-muted mt-1'>{stu_name} · {stu_info}</div>
          </div>
          <div class='d-flex gap-2 flex-wrap'>
            {status_chip}
            {review_chip}
          </div>
        </div>
        <div class='coread-step-grid'>
          <div class='coread-step coread-step--done'>1. 孩子已提交<span>閱讀心得與佐證</span></div>
          <div class='{parent_step_class}'>2. 家長補寫<span>共讀心得必填，照片可選</span></div>
          <div class='{teacher_step_class}'>3. 老師審核<span>完成後才會進入待審</span></div>
        </div>
        {coread_summary_html}
        <div class='coread-flow-note'>{flow_note}</div>
      </section>

      <div class='coread-grid'>
        <section class='coread-panel'>
          <div class='coread-panel-head'>
            <div class='coread-kicker'>孩子提交</div>
            <h4>孩子的閱讀內容</h4>
          </div>
          <div class='coread-panel-body'>
            <div class='coread-book'>
              <div class='coread-book-title'>《{book_title}》</div>
              <div class='coread-muted mt-1'>任務：{task_title}</div>
              <div class='coread-muted'>提交時間：{ts_txt}</div>
              {f"<div class='mt-2'>{task_desc}</div>" if task_desc else ""}
            </div>
            <div class='coread-mini-label'>孩子心得</div>
            <div class='coread-reflection'>{student_reflection}</div>
            <div class='coread-photo-list'>{student_photos}</div>
          </div>
        </section>

        <section class='coread-panel'>
          <div class='coread-panel-head'>
            <div class='coread-kicker'>家長回饋</div>
            <h4>家長共讀紀錄</h4>
          </div>
          <div class='coread-panel-body'>
            {msg_html}
            {coread_helper_html}
            <form method='post' enctype='multipart/form-data' class='coread-form-card'>
              <div class='mb-3'>
                <label class='form-label fw-bold'>家長心得</label>
                <textarea name='parent_reflection' rows='6' class='form-control' placeholder='可以寫孩子閱讀時的反應、一起討論到的內容、家長想給孩子的鼓勵。' required>{parent_ref_value}</textarea>
              </div>
              {parent_photo_preview}
              <div class='mb-2'>
                <label class='form-label fw-bold'>親子共讀照片（可選）</label>
                <input type='file' name='parent_photo' class='form-control' accept='.png,.jpg,.jpeg,.gif'>
                <div class='form-text'>{photo_hint}</div>
              </div>
              <div class='coread-actions'>
                <button class='btn btn-success coread-btn'>{submit_text}</button>
                <a class='btn btn-outline-secondary coread-btn' href='{dashboard_url}'>回家長工作頁</a>
              </div>
            </form>
          </div>
        </section>
      </div>
    </div>
    """
    return page("親子共讀回饋", content)

@app.route("/reading/withdraw/<int:ct_id>", methods=["POST"])
@login_required
def reading_withdraw(ct_id):
    """
    學生撤回未審核（pending）的閱讀認證：撤回 = 直接刪除
    - 僅限本人
    - 僅限 pending
    - 會同步嘗試刪除 uploads 內的照片檔（學生照片 + 家長照片）
    """
    import os

    ct = CompletedTask.query.get_or_404(ct_id)  

    
    if getattr(current_user, "role", "") != "student":
        return toast_redirect("home", "僅限學生可撤回閱讀認證。", "warning")  

    
    me = (getattr(current_user, "username", None) or getattr(current_user, "id", None))
    me = str(me) if me is not None else ""
    owner = str(getattr(ct, "student_name", "") or "")
    if not me or me != owner:
        return toast_redirect("home", "你無法撤回他人的閱讀認證。", "danger")  

    payload = _ct_load(ct) or {}
    st = (payload.get("status") or "pending").strip().lower()
    if st != "pending":
        return toast_redirect("reading_dashboard", "此筆已被處理，無法撤回。", "warning")  

    
    names = []

    
    for nm in (payload.get("photos") or []):
        if isinstance(nm, str) and nm.strip():
            names.append(nm.strip())

    
    nm = payload.get("parent_photo")
    if isinstance(nm, str) and nm.strip():
        names.append(nm.strip())

    
    if hasattr(ct, "proof_image") and getattr(ct, "proof_image", None):
        nm = str(getattr(ct, "proof_image"))
        if nm.strip():
            names.append(nm.strip())

    
    names = list(dict.fromkeys(names))

    
    db.session.delete(ct)
    db.session.commit()

    
    try:
        up_dir = app.config.get("UPLOAD_FOLDER") or UPLOAD_DIR
        for fn in names:
            
            fn = _safe_upload_name(fn)
            fp = os.path.join(up_dir, fn)
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass
    except Exception:
        pass

    
    task_id = payload.get("task_id") or getattr(ct, "task_id", None)
    if task_id:
        return toast_redirect(
            "reading_submit",
            "已撤回並刪除這筆尚未審核的閱讀認證。",
            "success",
            task_id=task_id
        )  

    return toast_redirect("reading_dashboard", "已撤回並刪除這筆閱讀認證。", "success")  

def _reading_review_apply_action(ct: "CompletedTask", action: str, rtag: str, reason: str = "") -> tuple[bool, str]:
    """套用單筆閱讀審核動作，供批次與單筆頁共用。"""
    from datetime import datetime

    u = _ct_user(ct)
    if not u or not can_manage_reading_class(current_user, getattr(u, "grade", None), getattr(u, "class_no", None)):
        return False, "沒有權限審核這筆閱讀認證。"

    payload = _ct_load(ct) or {}
    need_coread = reading_need_parent_coread(ct=ct, payload=payload, task=getattr(ct, "task", None))
    coread_done = reading_parent_coread_done(payload)

    if action == "delete":
        db.session.delete(ct)
        return True, "已刪除這筆閱讀認證。"

    payload["reflection_tag"] = rtag or payload.get("reflection_tag") or "none"

    if action == "reject":
        payload["status"] = "rejected"
        payload["reject_reason"] = reason or "未說明"
        _ct_save(ct, payload)
        db.session.add(ct)
        return True, "已退回這筆閱讀認證。"

    if action == "approve":
        if need_coread and not coread_done:
            return False, "這筆是親子共讀任務，需等家長補寫完成後才能通過。"

        sdg_codes = [int(x) for x in payload.get("sdg_codes", []) if _safe_int(x, 0) > 0]
        title = payload.get("book_title", "")
        isbn = payload.get("isbn", "")
        when_iso = payload.get("timestamp_iso")
        try:
            when = datetime.fromisoformat(when_iso) if when_iso else _now()
        except Exception:
            when = _now()
        preview = _compute_score_preview(u, payload["reflection_tag"], sdg_codes, title, isbn, when)
        parent_bonus = 10 if coread_done else 0
        breakdown = preview.get("breakdown", {})
        breakdown["parent_coread"] = parent_bonus
        payload["status"] = "approved"
        payload["score_total"] = _safe_int(preview.get("score_total", 0), 0) + parent_bonus
        payload["score_breakdown"] = breakdown
        payload["approved_by"] = current_user.username
        payload["approved_at"] = _now().isoformat()
        _ct_save(ct, payload)
        db.session.add(ct)
        return True, "已通過這筆閱讀認證。"

    return False, "未知的審核動作。"

@app.route("/reading/review", methods=["GET", "POST"])
@roles_required("teacher", "leader", "admin")
def reading_review_queue():
    from sqlalchemy.exc import IntegrityError
    from datetime import datetime
    
    unit, gno, cno = _user_scope()
    allowed_reading_classes = set()
    if getattr(current_user, "role", None) == "teacher":
        allowed_reading_classes = {
            (str(grade), str(class_no))
            for _value, _label, grade, class_no in _reading_class_options(current_user)
        }
        if not allowed_reading_classes:
            return page(
                "老師儀表板",
                "<div class='alert alert-warning'>目前沒有可檢視的閱讀班級。閱讀任務僅開放班導或閱讀授課教師使用。</div>",
            )
    msg = ""
    sel_ids = request.form.getlist("sel")
    action = request.form.get("action")

    
    def _need_parent_coread(ct, p: dict) -> bool:
        return reading_need_parent_coread(ct=ct, payload=p, task=getattr(ct, "task", None))

    def _parent_coread_completed(p: dict) -> bool:
        return reading_parent_coread_done(p)

    
    if request.method == "POST" and sel_ids and action in ("approve", "reject", "delete"):
        approved_cnt = rejected_cnt = deleted_cnt = skipped_wait_cnt = 0

        for sid in sel_ids:
            try:
                ct = CompletedTask.query.get(int(sid))  
            except Exception:
                ct = None
            if not ct:
                continue

            u = _ct_user(ct)
            
            if not u or not can_manage_reading_class(current_user, getattr(u, "grade", None), getattr(u, "class_no", None)):
                continue

            payload = _ct_load(ct) or {}

            if action != "delete" and _need_parent_coread(ct, payload) and not _parent_coread_completed(payload):
                skipped_wait_cnt += 1
                continue

            rtag = request.form.get(f"rtag_{ct.id}") or payload.get("reflection_tag") or "none"
            reason = request.form.get(f"reason_{ct.id}") or ""
            ok, _message = _reading_review_apply_action(ct, action, rtag, reason)
            if not ok:
                continue
            if action == "delete":
                deleted_cnt += 1
            elif action == "reject":
                rejected_cnt += 1
            elif action == "approve":
                approved_cnt += 1

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return toast_redirect("reading_review_queue", "操作失敗，請稍後再試。", "danger")  

        parts = []
        if approved_cnt:
            parts.append(f"通過 {approved_cnt} 筆")
        if rejected_cnt:
            parts.append(f"退回 {rejected_cnt} 筆")
        if deleted_cnt:
            parts.append(f"刪除 {deleted_cnt} 筆")
        if skipped_wait_cnt:
            parts.append(f"略過 {skipped_wait_cnt} 筆（等待家長補寫親子共讀）")

        msg_txt = "，".join(parts) if parts else "沒有任何變更。"
        return toast_redirect("reading_review_queue", msg_txt, "success")  

    
    col = ct_time_col()
    q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(  
        Task.task_type == "mission"
    )
    if col is not None:
        q = q.order_by(col.desc())
    else:
        q = q.order_by(CompletedTask.id.desc())

    recs = q.all()
    status_filter = (request.args.get("status") or "pending").strip()
    if status_filter not in ("pending", "approved", "rejected", "all"):
        status_filter = "pending"

    scoped_recs = []
    review_counts = {"pending": 0, "approved": 0, "rejected": 0, "waiting": 0, "all": 0}
    for ct in recs:
        u = _ct_user(ct)
        if not u or not can_manage_reading_class(current_user, getattr(u, "grade", None), getattr(u, "class_no", None)):
            continue
        payload_for_count = _ct_load(ct) or {}
        st_for_count = _ct_status(ct)
        if st_for_count == "pending" and _need_parent_coread(ct, payload_for_count) and not _parent_coread_completed(payload_for_count):
            review_counts["waiting"] += 1
        else:
            review_counts[st_for_count] = review_counts.get(st_for_count, 0) + 1
        review_counts["all"] += 1
        if status_filter == "all" or status_filter == st_for_count:
            scoped_recs.append(ct)

    
    waiting_coread_items = []

    def _row(ct):
        u = _ct_user(ct)
        if not u or not can_manage_reading_class(current_user, getattr(u, "grade", None), getattr(u, "class_no", None)):
            return ""

        p = _ct_load(ct) or {}
        st = _ct_status(ct)

        need_coread = _need_parent_coread(ct, p)
        coread_done = _parent_coread_completed(p)

        
        if st == "pending" and need_coread and not coread_done:
            
            try:
                ts_txt = _ct_get_timestamp(ct).strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts_txt = "—"
            waiting_coread_items.append({
                "id": ct.id,
                "student": display_name_of(u.username),
                "class": f"{getattr(u, 'grade', '')}年{getattr(u, 'class_no', '')}班",
                "time": ts_txt,
                "book": (p.get("book_title") or "未填書名").strip(),
                "task": getattr(ct.task, "title", "") or p.get("reading_task_title", "") or "永續閱讀任務",
                "reflection": (p.get("reflection_text") or "").strip(),
            })
            return ""

        
        if st == "pending":
            status_badge = "<span class='badge bg-warning text-dark ms-2'>待審核</span>"
        elif st == "approved":
            status_badge = "<span class='badge bg-success ms-2'>已通過</span>"
        else:
            status_badge = "<span class='badge bg-danger ms-2'>已退回</span>"

        
        imgs = ""
        for nm in p.get("photos", []):
            imgs += img_html(str(nm), maxw=180)
        if p.get("parent_photo"):
            nm = p["parent_photo"]
            imgs += img_html(str(nm), maxw=180)
        if imgs:
            imgs = f"<div class='review-attachment-grid'>{imgs}</div>"

        
        sdg_codes = p.get("sdg_codes", [])
        title = p.get("book_title", "")
        isbn = p.get("isbn", "")
        when_iso = p.get("timestamp_iso")
        when = datetime.fromisoformat(when_iso) if when_iso else _now()

        pv = _compute_score_preview(
            u,
            p.get("reflection_tag") or "none",
            sdg_codes,
            title,
            isbn,
            when,
        )

        parent_bonus = 10 if coread_done else 0
        total_estimated = pv.get("score_total", 0) + parent_bonus

        
        if not need_coread and not coread_done:
            coread_txt = "未設定親子共讀"
        elif need_coread and not coread_done:
            coread_txt = "老師指定，等待家長補寫"
        else:
            coread_txt = "家長已完成"

        sdgtxt = "、".join([f"{int(c):02d}" for c in p.get("sdg_codes", []) if _safe_int(c, 0) > 0])
        uname = display_name_of(u.username)
        reject_reason = (p.get("reject_reason") or "").strip()

        student_reflection = (p.get("reflection_text") or "").strip()
        parent_reflection = (p.get("parent_reflection") or "").strip()
        task_source_badge = (
            "<span class='badge bg-primary ms-2'>教師任務</span>"
            if is_assigned_task(ct.task) else
            "<span class='badge bg-success ms-2'>自主閱讀</span>"
        )
        task_title = escape(getattr(ct.task, "title", "") or p.get("reading_task_title", "") or "永續閱讀")

        abnormal = bool(p.get("abnormal_fast"))
        abnormal_badge = (
            "<span class='badge bg-danger'>異常速投</span>"
            if abnormal else
            "<span class='badge bg-secondary'>正常</span>"
        )

        reject_html = ""
        if st == "rejected" and reject_reason:
            reject_html = f"<div class='small text-danger mt-1'>退回原因：{reject_reason}</div>"

        parent_ref_html = ""
        if parent_reflection:
            parent_ref_html = f"<div class='small text-success mt-1'>家長心得：{parent_reflection}</div>"

        
        disabled = ""
        if need_coread and not coread_done:
            disabled = "disabled"

        return f"""
        <tr class='review-row'>
          <td class='text-nowrap align-top'>
            <div class='form-check mt-1'>
              <input class='form-check-input' type='checkbox' name='sel' value='{ct.id}' id='sel{ct.id}' {disabled}>
            </div>
          </td>
          <td>
            <div class='fw-bold'>
	              {uname}
	              <span class='text-muted small ms-2'>{_ct_get_timestamp(ct).strftime("%Y-%m-%d %H:%M")}</span>
	              {status_badge}{task_source_badge}
	            </div>
	            <div class='small'>任務：{task_title}</div>
	            <div class='small'>書名：{p.get('book_title','—')}</div>
            <div class='small'>
              SDG：{sdgtxt or '—'}　
              親子共讀狀態：{coread_txt}
            </div>
            <div class='small'>學生心得：{student_reflection or "（無）"}</div>
            {parent_ref_html}
            {reject_html}
            <div class='mt-1'>{imgs}</div>
          </td>
          <td class='text-nowrap align-top' style='width:14rem'>
            <select name='rtag_{ct.id}' class='form-select form-select-sm mb-2' {disabled}>
              <option value='none' {"selected" if (p.get("reflection_tag")=="none") else ""}>無（0）</option>
              <option value='some' {"selected" if (p.get("reflection_tag")=="some") else ""}>有寫（3）</option>
              <option value='great' {"selected" if (p.get("reflection_tag")=="great") else ""}>佳作（5）</option>
            </select>
            <input name='reason_{ct.id}' class='form-control form-control-sm'
                   placeholder='退回原因（退回時會儲存，可留空）' {disabled}>
          </td>
          <td class='text-end align-top'>
            <div class='small text-muted mb-1'>
              獲得分數：<b>{total_estimated}</b> 分
            </div>
            {abnormal_badge}
            <div class='mt-2'>
              <a class='btn btn-sm btn-outline-primary' href='{url_for("reading_review_detail", ct_id=ct.id)}'>詳細審核</a>
            </div>
          </td>
        </tr>
        """

    
    rows = "".join(_row(ct) for ct in scoped_recs) or "<tr><td colspan='4' class='text-center'>目前沒有符合條件的閱讀認證紀錄</td></tr>"

    
    waiting_html = ""
    if waiting_coread_items:
        cards = ""
        for item in waiting_coread_items[:20]:
            reflection = escape(item["reflection"][:90] + ("…" if len(item["reflection"]) > 90 else "")) if item["reflection"] else "孩子尚未留下心得摘要"
            cards += (
                "<article class='review-coread-card'>"
                "<div class='review-coread-main'>"
                f"<div class='review-coread-title'>{escape(item['student'])} · 《{escape(item['book'])}》</div>"
                f"<div class='review-coread-meta'>{escape(item['class'])} · {escape(item['task'])} · {escape(item['time'])}</div>"
                f"<div class='review-coread-reflection'>{reflection}</div>"
                "</div>"
                "<div class='review-coread-status'>"
                "<span class='badge bg-warning text-dark'>等待家長補寫</span>"
                "<span>完成後會自動進入上方待審清單</span>"
                f"<a class='btn btn-sm btn-outline-secondary mt-1' href='{url_for('reading_review_detail', ct_id=item['id'])}'>查看內容</a>"
                "</div>"
                "</article>"
            )
        waiting_html = f"""
        <style>
        .review-coread-waiting{{border-radius:24px;overflow:hidden;}}
        .review-coread-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;margin-bottom:.9rem;}}
        .review-coread-head h5{{margin:0;color:#162318;font-weight:900;}}
        .review-coread-list{{display:flex;flex-direction:column;gap:.7rem;}}
        .review-coread-card{{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;border-radius:18px;background:linear-gradient(135deg,#fff8e7 0%,#f7fff8 100%);border:1px solid rgba(209,142,36,.2);padding:1rem;}}
        .review-coread-title{{font-weight:900;color:#17231b;}}
        .review-coread-meta,.review-coread-reflection{{color:#647067;font-size:.9rem;margin-top:.18rem;}}
        .review-coread-status{{display:flex;flex-direction:column;align-items:flex-end;gap:.35rem;color:#718078;font-size:.84rem;font-weight:800;text-align:right;}}
        @media (max-width:767.98px){{.review-coread-card{{align-items:flex-start;}}.review-coread-status{{align-items:flex-start;text-align:left;}}}}
        </style>
        <div class='card border-0 shadow-sm mt-3 review-coread-waiting'>
          <div class='card-body'>
            <div class='review-coread-head'>
              <div>
                <h5>等待家長補寫親子共讀</h5>
                <div class='small text-muted mt-1'>這些閱讀紀錄已由學生送出，但家長心得尚未完成，因此暫時不開放老師批次通過。</div>
              </div>
              <span class='badge bg-light text-muted border'>共 {len(waiting_coread_items)} 筆</span>
            </div>
            <div class='review-coread-list'>{cards}</div>
          </div>
        </div>
        """

    def filter_link(label: str, value: str, count: int) -> str:
        active = " review-filter-chip--active" if status_filter == value else ""
        return f"<a class='review-filter-chip{active}' href='{url_for('reading_review_queue', status=value)}'>{label}<span>{count}</span></a>"

    filter_html = (
        "<div class='review-filter-row'>"
        + filter_link("待審核", "pending", review_counts.get("pending", 0))
        + filter_link("已通過", "approved", review_counts.get("approved", 0))
        + filter_link("已退回", "rejected", review_counts.get("rejected", 0))
        + filter_link("全部", "all", review_counts.get("all", 0))
        + f"<span class='review-filter-note'>等待家長補寫：{review_counts.get('waiting', 0)} 筆</span>"
        + "</div>"
    )

    summary_html = f"""
    <section class='review-summary-grid'>
      <div><span>待審核</span><strong>{review_counts.get('pending', 0)}</strong></div>
      <div><span>等待家長</span><strong>{review_counts.get('waiting', 0)}</strong></div>
      <div><span>已通過</span><strong>{review_counts.get('approved', 0)}</strong></div>
      <div><span>已退回</span><strong>{review_counts.get('rejected', 0)}</strong></div>
    </section>
    """

    flow_html = """
    <section class='review-flow-grid'>
      <article><strong>1</strong><div><b>查看內容</b><span>先確認學生心得、SDG 分類與佐證圖片是否完整。</span></div></article>
      <article><strong>2</strong><div><b>單筆或批次審核</b><span>需要細看時進入詳細審核；資料明確時可直接批次處理。</span></div></article>
      <article><strong>3</strong><div><b>留下結果</b><span>通過會計入閱讀分數，退回會保留原因讓學生修正。</span></div></article>
    </section>
    """

    form = f"""
    <div class='review-page'>
    <div class='review-hero'>
      <div>
        <div class='reading-submit-kicker'>Review</div>
        <h3>閱讀審核工作台</h3>
        <p>先檢查學生心得、SDG 與佐證圖片；若是親子共讀，需等家長補寫完成後才能通過。</p>
      </div>
      <a class='btn btn-outline-secondary' href='{url_for('teacher')}#teacher-reading-overview'>回老師工作頁</a>
    </div>
    {summary_html}
    {flow_html}
    <div class='card border-0 shadow-sm review-card'>
      <div class='card-body'>
        <div class='review-card-head'>
          <div>
            <h5 class='card-title mb-1'>老師閱讀認證管理</h5>
            <div class='small text-muted'>勾選資料後可批次通過、退回或刪除；退回時可在每列填寫原因。</div>
          </div>
          {filter_html}
        </div>
        {("<div class='alert alert-warning mb-2'>" + msg + "</div>") if msg else ""}
        <form method='post'>
          <div class='review-action-bar'>
            <label class='review-select-all'><input type='checkbox' id='reviewSelectAll'> 全選本頁</label>
            <button class='btn btn-success btn-sm' name='action' value='approve'>批次通過</button>
            <button class='btn btn-outline-danger btn-sm' name='action' value='reject'>批次退回</button>
            <button class='btn btn-outline-secondary btn-sm' name='action' value='delete'
                    onclick="return confirm('確定要刪除選取的閱讀認證紀錄嗎？此動作無法復原。');">
              批次刪除
            </button>
          </div>
          <div class='table-responsive'>
            <table class='table table-sm align-middle'>
              <thead>
                <tr>
                  <th style='width:3rem'></th>
                  <th>內容</th>
                  <th style='width:14rem'>標註 / 退回原因</th>
                  <th class='text-end' style='width:20rem'>獲得分數</th>
                </tr>
              </thead>
              <tbody>{rows}</tbody>
            </table>
          </div>
        </form>
      </div>
    </div>
    {waiting_html}
    <script>
      (function(){{
        var master = document.getElementById('reviewSelectAll');
        if (!master) return;
        master.addEventListener('change', function(){{
          document.querySelectorAll("input[name='sel']:not(:disabled)").forEach(function(cb){{ cb.checked = master.checked; }});
        }});
      }})();
    </script>
    </div>
    """

    head = f"<div class='small text-muted mb-2'>{unit or ''} · {gno}年{cno}班</div>"
    return page("閱讀審核／管理", head + form)  

@app.route("/reading/review/<int:ct_id>", methods=["GET", "POST"])
@roles_required("teacher", "leader", "admin")
def reading_review_detail(ct_id: int):
    ct = CompletedTask.query.get_or_404(ct_id)
    u = _ct_user(ct)
    if not u or not can_manage_reading_class(current_user, getattr(u, "grade", None), getattr(u, "class_no", None)):
        return toast_redirect("reading_review_queue", "沒有權限審核這筆閱讀認證。", "warning")

    payload = _ct_load(ct) or {}
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        rtag = request.form.get("reflection_tag") or payload.get("reflection_tag") or "none"
        reason = (request.form.get("reject_reason") or "").strip()
        if action == "reject" and not reason:
            return toast_redirect("reading_review_detail", "請先填寫退回原因。", "warning", ct_id=ct.id)
        ok, message = _reading_review_apply_action(ct, action, rtag, reason)
        if ok:
            db.session.commit()
            return toast_redirect("reading_review_queue", message, "success")
        db.session.rollback()
        return toast_redirect("reading_review_detail", message, "warning", ct_id=ct.id)

    task = getattr(ct, "task", None)
    payload = _ct_load(ct) or {}
    status = _ct_status(ct)
    need_coread = reading_need_parent_coread(ct=ct, payload=payload, task=task)
    coread_done = reading_parent_coread_done(payload)
    book_title = escape((payload.get("book_title") or "未填書名").strip())
    student_name = escape(display_name_of(u.username))
    class_text = escape(f"{getattr(u, 'unit', '') or ''} · {getattr(u, 'grade', '') or ''}年{getattr(u, 'class_no', '') or ''}班")
    task_title = escape(getattr(task, "title", "") or payload.get("reading_task_title", "") or "永續閱讀任務")
    task_desc = _desc_clean_html(task_body(task) if task else "")
    ts_txt = _ct_get_timestamp(ct).strftime("%Y-%m-%d %H:%M")
    sdg_codes = [int(c) for c in (payload.get("sdg_codes") or []) if _safe_int(c, 0) > 0]
    if not sdg_codes and _safe_int(payload.get("sdg_code"), 0) > 0:
        sdg_codes = [_safe_int(payload.get("sdg_code"), 0)]
    sdg_text = "、".join(f"SDG {c:02d}" for c in sdg_codes) or "未分類"
    reflection_text = _user_text_html((payload.get("reflection_text") or "學生尚未留下心得內容。").strip())
    parent_reflection = _user_text_html((payload.get("parent_reflection") or "").strip())
    reject_reason = _user_text_html((payload.get("reject_reason") or "").strip())

    def status_badge(st: str) -> str:
        if st == "approved":
            return "<span class='badge bg-success'>已通過</span>"
        if st == "rejected":
            return "<span class='badge bg-danger'>已退回</span>"
        return "<span class='badge bg-warning text-dark'>待審核</span>"

    coread_badge = (
        "<span class='badge bg-success'>親子共讀已完成</span>"
        if need_coread and coread_done else
        "<span class='badge bg-warning text-dark'>等待家長補寫</span>"
        if need_coread else
        "<span class='badge bg-light text-muted border'>一般閱讀</span>"
    )
    source_badge = (
        "<span class='badge bg-primary'>教師任務</span>"
        if task and is_assigned_task(task) else
        "<span class='badge bg-success'>自主閱讀</span>"
    )

    photo_html = ""
    for nm in (payload.get("photos") or []):
        photo_html += img_html(str(nm), maxw=360)
    parent_photo_html = img_html(payload.get("parent_photo"), maxw=360) if payload.get("parent_photo") else ""
    photo_block = photo_html or "<div class='review-detail-empty'>學生沒有上傳閱讀佐證圖片。</div>"
    parent_block = (
        "<section class='review-detail-panel'>"
        "<div class='review-detail-panel-head'><div><span>Parent Co-reading</span><h4>家長共讀回饋</h4></div>"
        f"{coread_badge}</div>"
        + (
            f"<div class='review-detail-text'>{parent_reflection}</div>"
            if parent_reflection else
            "<div class='review-detail-empty'>家長尚未補寫共讀心得。</div>"
        )
        + (f"<div class='review-detail-photos'>{parent_photo_html}</div>" if parent_photo_html else "")
        + "</section>"
        if need_coread else ""
    )

    rtag_current = payload.get("reflection_tag") or "some"
    try:
        when_iso = payload.get("timestamp_iso")
        when = datetime.fromisoformat(when_iso) if when_iso else _now()
    except Exception:
        when = _now()
    preview = _compute_score_preview(u, rtag_current, sdg_codes, payload.get("book_title", ""), payload.get("isbn", ""), when)
    parent_bonus = 10 if coread_done else 0
    estimated_total = _safe_int(preview.get("score_total", 0), 0) + parent_bonus
    breakdown = preview.get("breakdown", {}) or {}
    reflection_label_map = {
        "none": "無或內容不足（0 分）",
        "some": "有寫心得（3 分）",
        "great": "佳作心得（5 分）",
    }
    breakdown_label_map = {
        "complete": "基本完成",
        "reflection": "心得內容",
        "diversity_week": "本週 SDG 多元",
        "continuity_week": "連續閱讀",
        "parent_coread": "親子共讀加分",
    }
    score_cards = (
        "<div class='review-detail-score-grid'>"
        f"<div><span>心得標註</span><strong>{escape(reflection_label_map.get(rtag_current, rtag_current or '未標註'))}</strong></div>"
        f"<div><span>系統估算</span><strong>{_safe_int(preview.get('score_total', 0), 0)} 分</strong></div>"
        f"<div><span>親子共讀加分</span><strong>{parent_bonus} 分</strong></div>"
        f"<div><span>預估總分</span><strong>{estimated_total} 分</strong></div>"
        "</div>"
    )
    detail_items = "".join(
        f"<div class='review-detail-breakdown-row'><span>{escape(breakdown_label_map.get(str(k), str(k)))}</span><strong>{escape(str(v))} 分</strong></div>"
        for k, v in breakdown.items()
    ) or "<div class='review-detail-empty'>尚無細項分數。</div>"

    approve_disabled = " disabled" if (need_coread and not coread_done) else ""
    reject_reason_block = (
        f"<div class='review-detail-reject-note'><strong>目前退回原因</strong><div>{reject_reason}</div></div>"
        if reject_reason else ""
    )

    content = f"""
    <div class='review-detail-page'>
      <section class='review-detail-hero'>
        <div>
          <div class='reading-submit-kicker'>Single Review</div>
          <h3>{student_name} 的閱讀認證</h3>
          <p>{class_text} · {ts_txt}</p>
        </div>
        <div class='review-detail-hero-actions'>
          {status_badge(status)}
          {source_badge}
          <a class='btn btn-outline-secondary' href='{url_for("reading_review_queue")}'>回審核工作台</a>
        </div>
      </section>

      <section class='review-detail-summary'>
        <div><span>書名</span><strong>{book_title}</strong></div>
        <div><span>任務</span><strong>{task_title}</strong></div>
        <div><span>SDG</span><strong>{escape(sdg_text)}</strong></div>
        <div><span>親子共讀</span><strong>{'已完成' if coread_done else '待補寫' if need_coread else '未指定'}</strong></div>
      </section>

      <div class='review-detail-grid'>
        <section class='review-detail-panel'>
          <div class='review-detail-panel-head'>
            <div><span>Student Content</span><h4>學生提交內容</h4></div>
          </div>
          {f"<div class='review-detail-task-desc'>{task_desc}</div>" if task_desc else ""}
          <div class='review-detail-text'>{reflection_text}</div>
          <div class='review-detail-photos'>{photo_block}</div>
          {reject_reason_block}
        </section>

        <section class='review-detail-panel review-detail-panel--sticky'>
          <div class='review-detail-panel-head'>
            <div><span>Review Decision</span><h4>審核與給分</h4></div>
            <span class='badge bg-light text-muted border'>預估 {estimated_total} 分</span>
          </div>
          {score_cards}
          <div class='review-detail-breakdown'>{detail_items}</div>
          <form method='post' class='review-detail-form'>
            <label class='form-label'>心得標註</label>
            <select name='reflection_tag' class='form-select'>
              <option value='none' {'selected' if rtag_current == 'none' else ''}>無或內容不足（0）</option>
              <option value='some' {'selected' if rtag_current == 'some' else ''}>有寫心得（3）</option>
              <option value='great' {'selected' if rtag_current == 'great' else ''}>佳作心得（5）</option>
            </select>
            <label class='form-label mt-3'>退回原因</label>
            <textarea name='reject_reason' class='form-control' rows='3' placeholder='退回時必填，讓學生知道需要修正什麼。'></textarea>
            <div class='review-detail-actions'>
              <button class='btn btn-primary' name='action' value='approve'{approve_disabled}>通過認證</button>
              <button class='btn btn-outline-danger' name='action' value='reject'>退回修正</button>
              <button class='btn btn-outline-danger' name='action' value='delete' onclick="return confirm('確定刪除這筆閱讀認證？此動作無法復原。');">刪除</button>
            </div>
            {("<div class='review-detail-waiting'>親子共讀任務需等家長補寫完成後，才能通過。</div>" if need_coread and not coread_done else "")}
          </form>
        </section>
      </div>
      {parent_block}
    </div>
    """
    return page("單筆閱讀審核", content)

@app.route("/reading/dashboard")
@login_required
def reading_dashboard():
    from sqlalchemy import func
    from flask import url_for
    import json

    today_d = _today()
    ws, we = _week_range(today_d)
    week_pts, week_sdg = _user_week_points(current_user.id, ws, we)
    streak = _streak_weeks(current_user.id, today_d)

    
    week_goal  = globals().get("READING_WEEK_GOAL", 30)
    month_goal = globals().get("READING_MONTH_GOAL", 120)
    term_goal  = globals().get("READING_TERM_GOAL", 300)

    def _goal_int(v, default):
        return int(v) if isinstance(v, (int, float)) else default

    week_goal  = _goal_int(week_goal, 30)
    month_goal = _goal_int(month_goal, 120)
    term_goal  = _goal_int(term_goal, 300)

    
    def _pct(cur, goal):
        if not goal or goal <= 0:
            return 0
        return max(0, min(100, round(cur * 100 / goal)))

    
    q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(
        Task.task_type == "mission"
    )
    q = ct_user_filter(q, current_user.id)
    all_ct = q.all()

    total_pts = 0            
    latest_by_task: dict[int, CompletedTask] = {}

    for ct in all_ct:
        p = _ct_load(ct)
        st = _ct_status(ct)
        if st == "approved":
            sc = _safe_int(p.get("score_total", 0), 0)
            total_pts += sc

        
        tid = getattr(ct, "task_id", None)
        if tid is not None:
            prev = latest_by_task.get(tid)
            if (not prev) or _ct_get_timestamp(ct) >= _ct_get_timestamp(prev):
                latest_by_task[tid] = ct

    level = _level_of(total_pts)

    
    classmates = User.query.filter_by(
        role="student",
        unit=current_user.unit,
        grade=current_user.grade,
        class_no=current_user.class_no,
    ).all()

    rank = []
    weekly_class_totals = []
    for u in classmates:
        
        q_u = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(
            Task.task_type == "mission"
        )
        q_u = ct_user_filter(q_u, u.id)
        tot = 0
        for ct in q_u.all():
            if _ct_status(ct) == "approved":
                tot += _safe_int(_ct_load(ct).get("score_total", 0), 0)
        rank.append((tot, u))

        
        wpts, _ = _user_week_points(u.id, ws, we)
        weekly_class_totals.append(wpts)

    rank.sort(reverse=True, key=lambda x: x[0])
    class_size = len(rank)
    class_avg_total = round(sum(t for t, _ in rank) / class_size, 1) if class_size else 0.0
    class_week_avg = round(sum(weekly_class_totals) / len(weekly_class_totals), 1) if weekly_class_totals else 0.0

    self_rank = None
    self_total = 0
    for idx, (tot, u) in enumerate(rank, start=1):
        if u.id == current_user.id:
            self_rank = idx
            self_total = tot
            break

    

    
    month_pts = 0
    term_pts = total_pts

    
    if week_goal > 0:
        if week_pts >= week_goal:
            week_msg = f"本週已達標（目標 {week_goal} 分）。"
        else:
            week_msg = f"本週還差 <b>{week_goal - week_pts}</b> 分達標，加油！"
        week_label = f"{week_pts} / {week_goal} 分"
    else:
        week_msg = "老師尚未設定本週目標，先自由累積閱讀分數吧。"
        week_label = f"{week_pts} 分（未設定目標）"

    
    
    
    def _ensure_reading_mission():
        
        t = (
            Task.query.filter(
                Task.task_type == "mission",
                Task.unit == current_user.unit,
                Task.is_school_wide == 1,
            )
            .order_by(Task.start_date.asc(), Task.id.asc())
            .first()
        )
        if t:
            return t

        
        t = Task(
            title="永續閱讀認證（常駐）",
            description="本任務為全校自主閱讀認證入口，學生可不限次數完成閱讀認證。",
            created_by=getattr(current_user, "username", "system"),
            category=None,
            mission_category="永續閱讀",
            points=5,
            start_date=_today(),   
            end_date=None,
            unit=current_user.unit,
            grade=None,
            class_no=None,
            is_school_wide=1,
            task_type="mission",
            is_view_only=0,
        )
        db.session.add(t)
        db.session.commit()
        return t

    read_task = _ensure_reading_mission()
    read_link = url_for("reading_submit", task_id=read_task.id)

    
    
    
    SDG17 = {
        1: "無貧窮", 2: "零飢餓", 3: "健康與福祉", 4: "優質教育", 5: "性別平等",
        6: "淨水與衛生", 7: "可負擔及潔淨能源", 8: "合適工作與經濟成長",
        9: "產業創新與基礎建設", 10: "減少不平等", 11: "永續城鄉",
        12: "責任消費與生產", 13: "氣候行動", 14: "海洋生態",
        15: "陸域生態", 16: "和平正義與健全制度", 17: "夥伴關係",
    }

    m_first, m_last = _month_range(today_d)
    col = ct_time_col()
    m_ct_q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(
        Task.task_type == "mission"
    )
    m_ct_q = ct_user_filter(m_ct_q, current_user.id)
    if col is not None:
        m_ct_q = m_ct_q.filter(
            func.date(col) >= m_first.isoformat(),
            func.date(col) <= m_last.isoformat(),
        )
    m_cts = m_ct_q.all()

    sdg_scores = {code: 0 for code, _ in SDG_OPTIONS}
    sdg_name_map = {code: name for code, name in SDG_OPTIONS}
    month_pts = 0  

    for ct in m_cts:
        if _ct_status(ct) != "approved":
            continue
        p = _ct_load(ct)
        score = _safe_int(p.get("score_total", 0), 0)
        month_pts += score

        codes = []
        if p.get("sdg_codes"):
            codes = p["sdg_codes"]
        elif p.get("sdg_code"):
            codes = [p["sdg_code"]]

        used = set()
        for c in codes:
            v = _safe_int(c, 0)
            if v < 1 or v not in sdg_scores or v in used:
                continue
            used.add(v)
            
            sdg_scores[v] += score

    values = list(sdg_scores.values())
    month_total = month_pts  

    
    
    
    week_total_read = 0
    week_coread_done = 0
    week_full_bonus = 0

    month_total_read = 0
    month_coread_done = 0
    month_full_bonus = 0

    all_total_read = 0
    all_coread_done = 0
    all_full_bonus = 0
    last_coread_ts = None

    for ct in all_ct:
        if _ct_status(ct) != "approved":
            continue
        p = _ct_load(ct)
        parent_coread = reading_parent_coread_done(p)
        full_bonus = parent_coread

        ts = _ct_get_timestamp(ct)
        d = ts.date() if ts else None

        
        all_total_read += 1
        if parent_coread:
            all_coread_done += 1
            if (last_coread_ts is None) or (ts and ts > last_coread_ts):
                last_coread_ts = ts
        if full_bonus:
            all_full_bonus += 1

        
        if d and ws <= d <= we:
            week_total_read += 1
            if parent_coread:
                week_coread_done += 1
            if full_bonus:
                week_full_bonus += 1

        
        if d and m_first <= d <= m_last:
            month_total_read += 1
            if parent_coread:
                month_coread_done += 1
            if full_bonus:
                month_full_bonus += 1

    week_coread_pct = _pct(week_coread_done, week_total_read)
    month_coread_pct = _pct(month_coread_done, month_total_read)
    all_coread_pct = _pct(all_coread_done, all_total_read)
    all_full_pct = _pct(all_full_bonus, all_coread_done)

    if last_coread_ts:
        last_coread_str = last_coread_ts.strftime("%Y-%m-%d %H:%M")
    else:
        last_coread_str = "尚未有親子共讀紀錄"

    week_coread_label = (
        f"{week_coread_done} 筆 / {week_total_read} 筆（{week_coread_pct}%）"
        if week_total_read else "本週尚無閱讀紀錄"
    )
    month_coread_label = (
        f"{month_coread_done} 筆 / {month_total_read} 筆（{month_coread_pct}%）"
        if month_total_read else "本月尚無閱讀紀錄"
    )
    all_coread_label = (
        f"{all_coread_done} 筆 / {all_total_read} 筆（{all_coread_pct}%）"
        if all_total_read else "尚無閱讀紀錄"
    )
    full_coread_label = (
        f"{all_full_bonus} 筆（約佔親子共讀 {all_full_pct}%）"
        if all_coread_done else "尚未有完整親子共讀心得"
    )

    
    family_index = all_coread_pct

    
    
    
    if month_goal > 0:
        if month_pts >= month_goal:
            month_msg = f"本月已達標（目標 {month_goal} 分）。"
        else:
            month_msg = f"本月還差 <b>{month_goal - month_pts}</b> 分達標。"
        month_label = f"{month_pts} / {month_goal} 分"
    else:
        month_msg = "老師尚未設定本月目標，先按自己的步調累積閱讀分數。"
        month_label = f"{month_pts} 分（未設定目標）"

    term_pts = total_pts
    if term_goal > 0:
        if term_pts >= term_goal:
            term_msg = f"本學期已達標（目標 {term_goal} 分）。"
        else:
            term_msg = f"本學期還差 <b>{term_goal - term_pts}</b> 分達標。"
        term_label = f"{term_pts} / {term_goal} 分"
    else:
        term_msg = "老師尚未設定本學期目標，你可以把目前總分當作起點繼續努力。"
        term_label = f"{term_pts} 分（未設定目標）"

    
    week_pct  = _pct(week_pts, week_goal)
    month_pct = _pct(month_pts, month_goal)
    term_pct  = _pct(term_pts, term_goal)

    
    if week_goal > 0:
        class_week_pct = _pct(class_week_avg, week_goal)
        if class_week_avg >= week_goal:
            class_goal_badge = "<span class='badge bg-success'>已達成本週班級目標</span>"
            class_goal_desc  = f"全班本週平均約 {class_week_avg} 分，已超過老師設定的 {week_goal} 分。"
        else:
            diff = round(week_goal - class_week_avg, 1)
            class_goal_badge = "<span class='badge bg-warning text-dark'>尚未達成</span>"
            class_goal_desc  = f"全班本週平均約 {class_week_avg} 分，還差約 {diff} 分達到班級目標。"
        class_goal_text = f"目標：每人 {week_goal} 分　目前平均：約 {class_week_avg} 分"
    else:
        class_week_pct = 0
        class_goal_badge = "<span class='badge bg-secondary'>未設定目標</span>"
        class_goal_desc  = f"目前班級本週平均約 {class_week_avg} 分，老師尚未設定班級目標。"
        class_goal_text  = f"目前平均：約 {class_week_avg} 分"

    
    nonzero_vals = [v for v in values if v > 0]

    if nonzero_vals:
        max_v = max(nonzero_vals)
        min_v = min(nonzero_vals)

        
        uniformity = (min_v / max_v) if max_v > 0 else 0.0

        
        total_cats = len(SDG_OPTIONS) or 17
        coverage = len(nonzero_vals) / total_cats

        
        balance_index = round(100 * uniformity * coverage, 1)
    else:
        max_v = min_v = 0
        uniformity = 0.0
        coverage = 0.0
        balance_index = 0.0

    
    if month_total > 0:
        balance_weight = 0.5 + balance_index / 200.0
    else:
        balance_weight = 0.5

    has_balance_bonus = (month_total > 0 and balance_index >= 40.0)

    if month_total == 0:
        balance_status_text = "本月尚未有閱讀認證，尚未啟動加權。"
    elif has_balance_bonus:
        balance_status_text = (
            f"已啟動均衡加成，均衡係數約 {balance_weight:.2f}。"
            " 代表你的分數分布相對平均，閱讀越多、每一類別都補到，"
            "換算後的分數就越接近原始總分。"
            " 同時，每一個 SDG 類別的前兩本書一律採用原始分數，不受加權影響，"
            "從第三本開始才會套用「高分類別加分變慢、低分類別加分變快」的效果。"
        )
    else:
        balance_status_text = (
            f"目前尚未達均衡門檻（均衡指數低於 40%），均衡係數約 {balance_weight:.2f}。"
            " 代表某些類別分數很高，但其他類別太低：如果真的把分數拿去換算成「均衡後分數」，"
            "高分類別的加分會變慢，低分類別的加分會比較快——多補低分的類別，可以讓係數慢慢往 1.0 靠近。"
            " 並且，每一個 SDG 類別的前兩本書一律採用原始分數，不受加權影響，"
            "從第三本開始才會套用這種「高分加得比較慢、低分加得比較快」的差異。"
        )

    
    top_label = bottom_label = recommend_label = "—"
    if any(values):
        pairs = [(code, sdg_scores[code]) for code, _ in SDG_OPTIONS]
        max_code, max_val = max(pairs, key=lambda x: x[1])
        min_code, min_val = min(pairs, key=lambda x: x[1])
        top_label = f"{max_code:02d} {sdg_name_map[max_code]}（{max_val} 分）"
        bottom_label = f"{min_code:02d} {sdg_name_map[min_code]}（{min_val} 分）"
        recommend_label = f"{min_code:02d} {sdg_name_map[min_code]}"

    sdg_chart_html = _reading_sdg_chart_html(
        sdg_scores,
        sdg_name_map,
        "本月尚未有通過的閱讀認證，完成後會產生長條圖與圓餅圖。",
    )
    active_sdg_pairs = [(code, score) for code, score in sdg_scores.items() if score > 0]
    if active_sdg_pairs:
        top_sdg_code, top_sdg_score = max(active_sdg_pairs, key=lambda item: item[1])
        zero_sdg_codes = [code for code, score in sdg_scores.items() if score == 0]
        recommend_sdg_code = zero_sdg_codes[0] if zero_sdg_codes else min(sdg_scores.items(), key=lambda item: item[1])[0]
        top_sdg_label = f"{top_sdg_code:02d} {sdg_name_map[top_sdg_code]}"
        recommend_sdg_label = f"{recommend_sdg_code:02d} {sdg_name_map[recommend_sdg_code]}"
        sdg_focus_hint = f"本月最高 {top_sdg_score} 分，可以再補強 {recommend_sdg_label}。"
    else:
        top_sdg_label = "尚未累積"
        recommend_sdg_label = "先從喜歡的主題開始"
        sdg_focus_hint = "完成閱讀認證後，這裡會出現學生的 SDG 閱讀分布。"

    rank_label = f"第 {self_rank} / {class_size} 名" if self_rank else "尚未排序"
    class_rank_hint = f"全班本週平均 {class_week_avg} 分" if class_size else "目前尚無班級閱讀資料"

    def teacher_need_parent_coread(ct, payload: dict) -> bool:
        return reading_need_parent_coread(ct=ct, payload=payload, task=getattr(ct, "task", None))

    def teacher_parent_coread_done(payload: dict) -> bool:
        return reading_parent_coread_done(payload)

    approved_record_count = 0
    pending_cnt = 0
    rejected_cnt = 0
    coread_todo = 0
    coread_done_all = 0
    last_coread_ts = None
    for ct in all_ct:
        status = _ct_status(ct)
        payload = _ct_load(ct)
        if status == "approved":
            approved_record_count += 1
        elif status == "pending":
            pending_cnt += 1
        elif status == "rejected":
            rejected_cnt += 1
        if teacher_need_parent_coread(ct, payload) and not teacher_parent_coread_done(payload) and status != "rejected":
            coread_todo += 1
        if teacher_parent_coread_done(payload):
            coread_done_all += 1
            ts = _ct_get_timestamp(ct)
            if ts and ((last_coread_ts is None) or ts > last_coread_ts):
                last_coread_ts = ts
    coread_pct = _pct(coread_done_all, approved_record_count)
    coread_label = f"{coread_done_all} / {approved_record_count} 筆" if approved_record_count else "尚無紀錄"
    last_coread_label = last_coread_ts.strftime("%Y-%m-%d") if last_coread_ts else "尚未有親子共讀"
    balance_hint = "分布逐漸平均" if balance_index >= 40 else "可以多嘗試不同 SDG 主題"

    
    if streak > 0:
        streak_msg = f"你已經連續 {streak} 週有閱讀認證紀錄，維持一點點的閱讀習慣就很棒。"
    else:
        streak_msg = "這學期還沒有連續閱讀紀錄，從本週開始完成一次閱讀認證試試看。"

    assigned_cards = []
    for task in _assigned_reading_tasks_for_student(current_user)[:8]:
        meta = task_meta(task)
        latest = latest_by_task.get(task.id) or _latest_submission_for_task(task.id, current_user.username)
        status_html = "<span class='badge bg-secondary'>尚未提交</span>"
        action_label = "開始任務"
        action_class = "btn-primary"
        if latest:
            st = _ct_status(latest)
            if st == "approved":
                status_html = "<span class='badge bg-success'>已通過</span>"
                action_label = "查看紀錄"
                action_class = "btn-outline-success"
            elif st == "pending":
                status_html = "<span class='badge bg-warning text-dark'>待審核</span>"
                action_label = "查看提交"
                action_class = "btn-outline-secondary"
            else:
                status_html = "<span class='badge bg-danger'>已退回，可重交</span>"
                action_label = "重新提交"
                action_class = "btn-outline-danger"

        expired = bool(task.end_date and today_d > task.end_date and not latest)
        action_html = (
            "<span class='btn btn-sm btn-outline-secondary disabled'>已截止</span>"
            if expired else
            f"<a class='btn btn-sm {action_class}' href='{url_for('reading_submit', task_id=task.id)}'>{action_label}</a>"
        )
        source = escape(meta.get(META_SOURCE, "") or "未指定")
        parent_badge = "<span class='badge bg-info ms-1'>親子共讀</span>" if meta.get(META_PARENT) == "1" else ""
        assigned_cards.append(
            "<div class='col-md-6'>"
            "<div class='card border-0 shadow-sm h-100'>"
            "<div class='card-body'>"
            "<div class='d-flex justify-content-between align-items-start gap-2 mb-2'>"
            f"<div><div class='fw-semibold'>{escape(task.title)}</div>"
            f"<div class='small text-muted'>{scope_txt(task)} · 截止 {task.end_date or '不限'}</div></div>"
            f"<div class='text-nowrap'>{status_html}{parent_badge}</div>"
            "</div>"
            f"<div class='small text-muted mb-2'>{_desc_clean_html(task_body(task))}</div>"
            f"<div class='small mb-1'>SDG：{_sdg_label(meta.get(META_SDG_CODE))}</div>"
            f"<div class='small mb-1'>閱讀來源：{source}</div>"
            "<div class='small text-muted mb-3'>完成後請送出閱讀心得，老師會依內容進行審核。</div>"
            f"<div class='text-end'>{action_html}</div>"
            "</div></div></div>"
        )

    assigned_tasks_html = f"""
      <div class='col-12'>
        <div class='d-flex justify-content-between align-items-center mb-2'>
          <h5 class='mb-0'>老師指定閱讀任務</h5>
          <span class='small text-muted'>共 {len(assigned_cards)} 項</span>
        </div>
        <div class='row g-3'>
          {''.join(assigned_cards) or "<div class='col-12'><div class='alert alert-light border mb-0'>目前沒有老師指定的永續閱讀任務，你仍可使用自主閱讀累積紀錄。</div></div>"}
        </div>
      </div>
    """

    html = f"""
    <div class='row g-3'>

      <!-- 永續閱讀專區（自主閱讀） -->
      <div class='col-12'>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <div class='d-flex justify-content-between align-items-center mb-2'>
              <h5 class='card-title mb-0'>永續閱讀專區（自主閱讀）</h5>
              <a class='btn btn-sm btn-success' href='{read_link}'>前往閱讀認證</a>
            </div>
          </div>
        </div>
	      </div>

      {assigned_tasks_html}

      <!-- 我的閱讀進度 -->
      <div class='col-12'>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <h5 class='card-title mb-2'>我的閱讀進度</h5>
            <div class='row g-3'>
              <div class='col-md-6'>

                <div class='mb-3'>
                  <div class='d-flex justify-content-between'>
                    <span>本週進度</span>
                    <span class='small text-muted'>{week_label}</span>
                  </div>
                  <div class='progress' style='height:0.8rem;'>
                    <div class='progress-bar bg-success'
                         role='progressbar'
                         style='width:{week_pct}%;'
                         aria-valuenow='{week_pct}' aria-valuemin='0' aria-valuemax='100'></div>
                  </div>
                  <div class='small text-muted mt-1'>{week_msg}</div>
                </div>

                <div class='mb-3'>
                  <div class='d-flex justify-content-between'>
                    <span>本月進度</span>
                    <span class='small text-muted'>{month_label}</span>
                  </div>
                  <div class='progress' style='height:0.8rem;'>
                    <div class='progress-bar bg-info'
                         role='progressbar'
                         style='width:{month_pct}%;'
                         aria-valuenow='{month_pct}' aria-valuemin='0' aria-valuemax='100'></div>
                  </div>
                  <div class='small text-muted mt-1'>{month_msg}</div>
                </div>

                <div class='mb-1'>
                  <div class='d-flex justify-content-between'>
                    <span>本學期進度</span>
                    <span class='small text-muted'>{term_label}</span>
                  </div>
                  <div class='progress' style='height:0.8rem;'>
                    <div class='progress-bar bg-primary'
                         role='progressbar'
                         style='width:{term_pct}%;'
                         aria-valuenow='{term_pct}' aria-valuemin='0' aria-valuemax='100'></div>
                  </div>
                  <div class='small text-muted mt-1'>{term_msg}</div>
                </div>

              </div>
              <div class='col-md-6'>
                <div class='alert alert-warning mb-2'>
                  連續週數：<b>{streak}</b>
                </div>
                <div class='small text-muted mb-3'>
                  {streak_msg}
                </div>
                <div class='alert alert-info mb-2'>
                  等級：<b>{level}</b>（歷史 {total_pts} 分）
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 親子共讀概況 -->
      <div class='col-12'>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <h5 class='card-title mb-2'>親子共讀概況</h5>
            <div class='row g-3'>
              <div class='col-md-6'>
                <div class='mb-2'>
                  <div class='small text-muted mb-1'>本週親子共讀完成率（以本週已核准閱讀紀錄為底）</div>
                  <div class='alert alert-success mb-1'>
                    本週親子共讀：<b>{week_coread_label}</b>
                  </div>
                </div>
                <div class='mb-2'>
                  <div class='small text-muted mb-1'>本月親子共讀完成率</div>
                  <div class='alert alert-info mb-1'>
                    本月親子共讀：<b>{month_coread_label}</b>
                  </div>
                </div>
                <div class='mb-1'>
                  <div class='small text-muted mb-1'>本學期家長參與指數</div>
                  <div class='alert alert-secondary mb-1'>
                    親子共讀比例：約 <b>{family_index}%</b>（{all_coread_label}）
                  </div>
                </div>
              </div>
              <div class='col-md-6'>
                <div class='mb-2'>
                  <div class='small text-muted mb-1'>完整親子共讀（老師指定 + 家長心得）</div>
                  <div class='alert alert-warning mb-1'>
                    {full_coread_label}
                  </div>
                </div>
                <div class='mb-2'>
                  <div class='small text-muted mb-1'>最近一次親子共讀時間</div>
                  <div class='h6 mb-0'>{last_coread_str}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 班級概況 -->
      <div class='col-12'>
        <h6 class='mb-2'>班級概況</h6>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <div class='row g-3'>
              <div class='col-md-6'>
                <div class='small text-muted mb-1'>你的班級名次</div>
                <div class='h5 mb-1'>{self_rank or "—"} / {class_size}</div>
                <div class='small text-muted'>
                  你的總分：{self_total} 分；班級平均總分：約 {class_avg_total} 分。
                </div>
              </div>
              <div class='col-md-6'>
                <div class='small text-muted mb-1'>本週班級目標達成狀態</div>
                <div class='d-flex justify-content-between align-items-center mb-1 flex-wrap'>
                  <div class='small mb-1 mb-sm-0'>{class_goal_text}</div>
                  <div>{class_goal_badge}</div>
                </div>
                <div class='progress' style='height:0.6rem;'>
                  <div class='progress-bar bg-success'
                       role='progressbar'
                       style='width:{class_week_pct}%;'
                       aria-valuenow='{class_week_pct}' aria-valuemin='0' aria-valuemax='100'></div>
                </div>
                <div class='small text-muted mt-1'>
                  {class_goal_desc}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- SDG 17 類分布 -->
      <div class='col-12'>
        <h6 class='mb-2'>SDG 17 類別分布（本月）</h6>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <div class='row g-3 align-items-center'>
              <div class='col-md-4'>
                <div class='mb-1 small text-muted'>本月閱讀總分（未加權）：</div>
                <div class='h5 mb-1'>{month_total} 分</div>
                <div class='small text-muted mb-3'>{month_msg}</div>

                <div class='mb-1 small text-muted'>均衡指數：</div>
                <div class='display-6'>{balance_index:.1f}<span class='fs-6'>%</span></div>
                <div class='small text-muted mt-3'>
                  最高分類：{top_label}<br>
                  最低分類：{bottom_label}
                </div>
                <div class='small text-primary mt-2'>
                  推薦補強類別：{recommend_label}
                </div>

                <hr class='my-3'>

                <div class='small text-muted'>
                  目前加權狀態：{balance_status_text}
                </div>
              </div>
              <div class='col-md-4'>
                <div style='height:260px;'>
                  <canvas id='sdgPie'></canvas>
                </div>
              </div>
              <div class='col-md-4'>
                <div style='height:260px;'>
                  <canvas id='sdgBar'></canvas>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    """
    chart_pairs = [
        (f"{code:02d} {name}", sdg_scores.get(code, 0))
        for code, name in SDG_OPTIONS
        if sdg_scores.get(code, 0) > 0
    ]
    labels_js = json.dumps([label for label, _score in chart_pairs], ensure_ascii=False)
    data_js = json.dumps([score for _label, score in chart_pairs], ensure_ascii=False)
    charts_js = f"""
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
    (function() {{
      const labels = {labels_js};
      const data = {data_js};

      const pieCtx = document.getElementById('sdgPie');
      const barCtx = document.getElementById('sdgBar');
      if (!pieCtx || !barCtx) return;

      // 統一色系：藍色系，明度由淺到深
      const colors = labels.map((_, idx) => {{
        const light = 30 + idx * 2; // 30% ~ 約 64%
        return 'hsl(210, 65%, ' + light + '%)';
      }});

      new Chart(pieCtx, {{
        type: 'pie',
        data: {{
          labels: labels,
          datasets: [{{
            data: data,
            backgroundColor: colors,
            borderColor: '#ffffff',
            borderWidth: 1
          }}]
        }},
        options: {{
          plugins: {{
            legend: {{
              position: 'bottom',
              labels: {{
                boxWidth: 10,
                font: {{ size: 11 }}
              }}
            }}
          }}
        }}
      }});

      new Chart(barCtx, {{
        type: 'bar',
        data: {{
          labels: labels,
          datasets: [{{
            label: '本月各類得分',
            data: data,
            backgroundColor: 'rgba(54, 162, 235, 0.7)',
            borderColor: 'rgba(54, 162, 235, 1)',
            borderWidth: 1,
            maxBarThickness: 18
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            x: {{
              ticks: {{
                autoSkip: true,
                maxRotation: 45,
                minRotation: 45,
                font: {{ size: 10 }}
              }}
            }},
            y: {{
              beginAtZero: true
            }}
          }},
          plugins: {{
            legend: {{
              display: false
            }}
          }}
        }}
      }});
    }})();
    </script>
    """
    return page("學生儀表板", html + charts_js)

@app.route("/reading/teacher_dashboard")
@roles_required("teacher", "leader", "admin")
def reading_teacher_dashboard():
    from sqlalchemy import func
    from collections import defaultdict
    from flask import url_for

    
    unit, gno, cno = _user_scope()
    allowed_reading_classes = set()
    if getattr(current_user, "role", None) == "teacher":
        allowed_reading_classes = {
            (str(grade), str(class_no))
            for _value, _label, grade, class_no in _reading_class_options(current_user)
        }
        if not allowed_reading_classes:
            return page(
                "閱讀任務儀表板",
                "<div class='alert alert-warning'>目前沒有可檢視的閱讀班級。閱讀任務僅開放班導或閱讀授課教師使用。</div>",
            )

    
    def display_name_of(username: str) -> str:
        try:
            u = User.query.filter_by(username=username).first()  
            return (u.display_name or u.username) if u else (username or "")
        except Exception:
            return username or ""

    students_q = User.query.filter_by(unit=unit, role="student")  
    if getattr(current_user, "role", None) == "teacher":
        students = [
            s for s in students_q.order_by(User.username.asc()).all()
            if (str(s.grade), str(s.class_no)) in allowed_reading_classes
        ]
    else:
        if gno:
            students_q = students_q.filter_by(grade=gno)
        if cno:
            students_q = students_q.filter_by(class_no=cno)
        students = students_q.order_by(User.username.asc()).all()
    stu_ids = [u.id for u in students]
    class_size = len(students)

    today_d = _today()
    ws, we = _week_range(today_d)

    
    raw_week_goal = globals().get("READING_WEEK_GOAL", None)
    if raw_week_goal is None:
        raw_week_goal = globals().get("READING_TERM_GOAL", 30)

    try:
        week_goal = max(0, int(raw_week_goal or 0))
    except Exception:
        week_goal = 0

    
    per_week_pts: dict[int, int] = {}
    sum_week_pts = 0
    count_any = 0           
    count_goal = 0          

    for s in students:
        wpts, _ = _user_week_points(s.id, ws, we)
        wpts = int(wpts or 0)
        per_week_pts[s.id] = wpts
        sum_week_pts += wpts
        if wpts > 0:
            count_any += 1
        if week_goal > 0 and wpts >= week_goal:
            count_goal += 1

    class_avg_week = round(sum_week_pts / class_size, 1) if class_size else 0.0
    any_rate  = f"{(count_any  * 100 / class_size):.0f}%" if class_size else "—"
    goal_rate = f"{(count_goal * 100 / class_size):.0f}%" if class_size and week_goal > 0 else "—"

    any_pct_val  = int(count_any  * 100 / class_size) if class_size else 0
    goal_pct_val = int(count_goal * 100 / class_size) if class_size and week_goal > 0 else 0

    
    col = ct_time_col()
    cts_q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(  
        Task.task_type == "mission"
    )
    if col is not None:
        cts_q = cts_q.filter(
            func.date(col) >= ws.isoformat(),
            func.date(col) <= we.isoformat(),
        )
    cts = cts_q.all()

    sdg_counter = defaultdict(int)

    coread_total_records = 0          
    coread_with_parent = 0           
    coread_full_bonus = 0            
    per_stu_coread = defaultdict(lambda: dict(any=0, full=0))

    for ct in cts:
        u = _ct_user(ct)
        if not u or u.id not in stu_ids:
            continue
        if _ct_status(ct) != "approved":
            continue

        p = _ct_load(ct)

        
        codes = []
        if p.get("sdg_codes"):
            codes = p["sdg_codes"]
        elif p.get("sdg_code"):
            codes = [p["sdg_code"]]

        used = set()
        for s_code in codes:
            s2 = _safe_int(s_code, 0)
            if s2 <= 0 or s2 in used:
                continue
            used.add(s2)
            sdg_counter[s2] += 1

        
        coread_total_records += 1
        parent_coread = reading_parent_coread_done(p)
        full_ok = parent_coread

        if parent_coread:
            coread_with_parent += 1
            per_stu_coread[u.id]["any"] += 1
        if full_ok:
            coread_full_bonus += 1
            per_stu_coread[u.id]["full"] += 1

    max_ct = max(sdg_counter.values()) if sdg_counter else 0
    sorted_sdg = sorted(
        [(code, sdg_counter.get(code, 0)) for code, _ in SDG_OPTIONS],
        key=lambda x: x[1]
    )
    weak = {code for code, _ in sorted_sdg[:4]}  

    def _cell(code: int, name: str) -> str:
        v = sdg_counter.get(code, 0)
        bg = "#f8f9fa"
        if max_ct > 0 and v > 0:
            intensity = int((v / max_ct) * 100)
            bg = f"hsl(200, 70%, {90 - intensity // 2}%)"
        tag = "<span class='badge bg-danger ms-1'>弱項</span>" if code in weak and max_ct > 0 else ""
        return (
            f"<div class='p-2 rounded mb-2' style='background:{bg}'>"
            f"{code:02d} {name} <span class='float-end'>{v}</span>{tag}</div>"
        )

    cells_list = [_cell(code, name) for code, name in SDG_OPTIONS]
    half = (len(cells_list) + 1) // 2
    col1, col2 = "".join(cells_list[:half]), "".join(cells_list[half:])

    
    if coread_total_records > 0:
        coread_rate = round(coread_with_parent * 100.0 / coread_total_records, 1)
        coread_full_rate = round(coread_full_bonus * 100.0 / coread_total_records, 1)
    else:
        coread_rate = coread_full_rate = 0.0

    
    coread_top = sorted(
        [(per_stu_coread[u.id]["any"], u) for u in students if per_stu_coread[u.id]["any"] > 0],
        key=lambda x: x[0],
        reverse=True,
    )[:5]

    if coread_top:
        top_rows = []
        for idx, (cnt, stu) in enumerate(coread_top, start=1):
            name = display_name_of(stu.username)
            detail_url = url_for("reading_teacher_student", student_username=stu.username)
            top_rows.append(
                "<tr>"
                f"<td class='text-center'>{idx}</td>"
                f"<td class='text-nowrap'><a href='{detail_url}'>{name}</a></td>"
                f"<td class='text-end'>{cnt}</td>"
                "</tr>"
            )
        coread_top_html = (
            "<div class='mt-3'>"
            "<div class='small text-muted mb-1'>本週親子共讀次數較多的學生：</div>"
            "<div class='table-responsive'>"
            "<table class='table table-sm align-middle mb-0'>"
            "<thead><tr>"
            "<th style='width:3rem' class='text-center'>名次</th>"
            "<th>學生</th>"
            "<th class='text-end'>親子共讀次數</th>"
            "</tr></thead>"
            f"<tbody>{''.join(top_rows)}</tbody></table></div></div>"
        )
    else:
        coread_top_html = "<div class='mt-3 small text-muted'>本週尚無親子共讀紀錄。</div>"

    coread_block = f"""
      <div class='col-12'>
        <h6 class='mb-2'>親子共讀概況（本週）</h6>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <div class='row g-3'>
              <div class='col-md-4'>
                <div class='alert alert-success mb-2'>
                  有親子共讀的閱讀紀錄：<b>{coread_with_parent}</b> 筆
                </div>
                <div class='small text-muted'>
                  以本週已核准的閱讀認證為基準，統計老師指定為「親子共讀」的紀錄數。
                </div>
              </div>
              <div class='col-md-4'>
                <div class='alert alert-primary mb-2'>
                  親子共讀比例：約 <b>{coread_rate:.1f}%</b>
                </div>
                <div class='small text-muted'>
                  比例 = （有親子共讀的紀錄 ÷ 本週所有核准閱讀紀錄）× 100。
                </div>
              </div>
              <div class='col-md-4'>
                <div class='alert alert-info mb-2'>
                  完整親子共讀心得：<b>{coread_full_bonus}</b> 筆
                </div>
                <div class='small text-muted'>
                  家長完成共讀心得後即可列入完整紀錄；照片可作為親師交流時的補充佐證。
                </div>
              </div>
            </div>
            {coread_top_html}
          </div>
        </div>
      </div>
    """

    
    class_rows = []
    for s in students:
        name = display_name_of(s.username)
        pts = per_week_pts.get(s.id, 0)
        if week_goal > 0:
            if pts >= week_goal:
                goal_badge = "<span class='badge bg-success'>已達標</span>"
            else:
                goal_badge = "<span class='badge bg-secondary'>未達標</span>"
        else:
            goal_badge = "<span class='badge bg-light text-muted'>未設定</span>"
        coread_cnt = per_stu_coread[s.id]["any"]
        detail_url = url_for("reading_teacher_student", student_username=s.username)
        class_rows.append(
            "<tr>"
            f"<td class='text-nowrap'><a href='{detail_url}'>{name}</a></td>"
            f"<td class='text-end'>{pts}</td>"
            f"<td class='text-center'>{goal_badge}</td>"
            f"<td class='text-end'>{coread_cnt}</td>"
            "</tr>"
        )

    if class_rows:
        class_table_html = (
            "<div class='table-responsive'>"
            "<table class='table table-sm align-middle mb-0'>"
            "<thead><tr>"
            "<th>學生</th>"
            "<th class='text-end'>本週分數</th>"
            "<th class='text-center'>是否達標</th>"
            "<th class='text-end'>本週親子共讀次數</th>"
            "</tr></thead>"
            f"<tbody>{''.join(class_rows)}</tbody></table></div>"
        )
    else:
        class_table_html = "<div class='small text-muted'>目前班級內尚無學生帳號，無法顯示本週概況。</div>"

    class_table_block = f"""
      <div class='col-12'>
        <h6 class='mb-2'>班級學生本週閱讀一覽</h6>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            {class_table_html}
            <div class='small text-muted mt-2'>
              提示：點選學生姓名可查看「個別學生閱讀分析」，包含親子共讀與 SDG 分布。
            </div>
          </div>
        </div>
      </div>
    """

    
    if week_goal > 0 and class_size:
        weak_rows = []
        for s in students:
            pts = per_week_pts.get(s.id, 0)
            if pts < week_goal:
                detail_url = url_for("reading_teacher_student", student_username=s.username)
                weak_rows.append(
                    "<tr>"
                    f"<td class='text-nowrap'><a href='{detail_url}'>{display_name_of(s.username)}</a></td>"
                    f"<td class='text-end'>{pts}</td>"
                    "</tr>"
                )
        if weak_rows:
            weak_table_html = (
                "<div class='table-responsive'><table class='table table-sm align-middle mb-0'>"
                "<thead><tr><th>學生</th><th class='text-end'>本週分數</th></tr></thead>"
                f"<tbody>{''.join(weak_rows)}</tbody></table></div>"
            )
        else:
            weak_table_html = "<div class='text-muted small'>本班所有學生都已達成本週閱讀目標。</div>"
    else:
        weak_table_html = (
            "<div class='text-muted small'>尚未設定「本週閱讀目標」，"
            "請先到「閱讀目標設定」頁設定本週每人目標分數。</div>"
        )

    
    if getattr(current_user, "role", None) == "teacher" and len(allowed_reading_classes) > 1:
        class_info = f"{unit or ''} · 可管理閱讀班級 {len(allowed_reading_classes)} 個"
    elif getattr(current_user, "role", None) == "teacher" and allowed_reading_classes:
        one_g, one_c = sorted(allowed_reading_classes, key=lambda x: (_safe_int(x[0], 999), _safe_int(x[1], 999)))[0]
        class_info = f"{unit or ''} · {one_g}年{one_c}班"
    else:
        class_info = f"{unit or ''} · {gno}年{cno}班" if gno and cno else (unit or "")

    html = f"""
    <div class='row g-3'>

      <!-- 上方總覽：本週班級概況 -->
      <div class='col-12'>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <div class='d-flex justify-content-between align-items-center mb-2'>
              <div>
                <h5 class='card-title mb-0'>老師閱讀儀表板（本週）</h5>
                <div class='small text-muted'>{class_info} · {ws} ~ {we}</div>
              </div>
            </div>
            <div class='row g-2'>
              <div class='col-md-4'>
                <div class='alert alert-success mb-2'>
                  本週有閱讀認證：<b>{count_any} / {class_size}</b> 人（{any_rate}）
                </div>
                <div class='small text-muted'>只要本週至少完成一筆閱讀認證就算在內。</div>
              </div>
              <div class='col-md-4'>
                <div class='alert alert-primary mb-2'>
                  達成本週目標：<b>{count_goal} / {class_size}</b> 人（{goal_rate}）
                </div>
                <div class='small text-muted'>
                  以「閱讀目標設定」中的每人本週目標 <b>{week_goal}</b> 分為標準。
                </div>
              </div>
              <div class='col-md-4'>
                <div class='alert alert-info mb-2'>
                  班級本週平均：<b>{class_avg_week:.1f}</b> 分 / 人
                </div>
                <div class='small text-muted'>
                  全班本週總分 {sum_week_pts} 分 ÷ 班級 {class_size} 人。
                </div>
              </div>
            </div>

            <div class='row g-3 mt-3'>
              <div class='col-md-6'>
                <div class='small text-muted mb-1'>達成本週目標比例</div>
                <div class='progress' style='height: 0.75rem;'>
                  <div class='progress-bar bg-success' role='progressbar'
                       style='width: {goal_pct_val}%;'
                       aria-valuenow='{goal_pct_val}' aria-valuemin='0' aria-valuemax='100'></div>
                </div>
              </div>
              <div class='col-md-6'>
                <div class='small text-muted mb-1'>本週有閱讀認證比例</div>
                <div class='progress' style='height: 0.75rem;'>
                  <div class='progress-bar bg-info' role='progressbar'
                       style='width: {any_pct_val}%;'
                       aria-valuenow='{any_pct_val}' aria-valuemin='0' aria-valuemax='100'></div>
                </div>
              </div>
            </div>

          </div>
        </div>
      </div>

      {coread_block}
      {class_table_block}

      <!-- SDG 熱力圖（本週認證數量） -->
      <div class='col-12'>
        <h6 class='mb-2'>SDG 熱力圖（本週認證數量）</h6>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <div class='row'>
              <div class='col-md-6'>{col1}</div>
              <div class='col-md-6'>{col2}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 本週尚未達成本週目標的學生 -->
      <div class='col-12'>
        <h6 class='mb-2'>本週尚未達成本週目標的學生</h6>
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            {weak_table_html}
          </div>
        </div>
      </div>

    </div>
    """
    return page("老師儀表板", html)  

@app.route("/reading/teacher_student/<student_username>")
@roles_required("teacher", "leader", "admin")
def reading_teacher_student(student_username):
    from sqlalchemy import func
    import json

    
    stu = User.query.filter_by(username=student_username, role="student").first()
    if not stu:
        return toast_redirect("reading_teacher_dashboard", "找不到這位學生。", "warning")

    
    if getattr(current_user, "role", None) not in ("leader", "admin"):
        if not can_manage_reading_class(current_user, getattr(stu, "grade", None), getattr(stu, "class_no", None)):
            return toast_redirect("reading_teacher_dashboard", "您無權查看此學生的閱讀資料。", "warning")

    stu_name = getattr(stu, "display_name", None) or getattr(stu, "username", "")
    class_info = f"{getattr(stu, 'unit', '')} · {getattr(stu, 'grade', '')}年{getattr(stu, 'class_no', '')}班"

    today_d = _today()
    ws, we = _week_range(today_d)
    week_pts, week_sdg = _user_week_points(stu.id, ws, we)
    streak = _streak_weeks(stu.id, today_d)

    
    week_goal  = globals().get("READING_WEEK_GOAL", 30)
    month_goal = globals().get("READING_MONTH_GOAL", 120)
    term_goal  = globals().get("READING_TERM_GOAL", 300)

    def _goal_int(v, default):
        return int(v) if isinstance(v, (int, float)) else default

    week_goal  = _goal_int(week_goal, 30)
    month_goal = _goal_int(month_goal, 120)
    term_goal  = _goal_int(term_goal, 300)

    def _pct(cur, goal):
        if not goal or goal <= 0:
            return 0
        return max(0, min(100, round(cur * 100 / goal)))

    
    q_all = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(
        Task.task_type == "mission"
    )
    q_all = ct_user_filter(q_all, stu.id)
    all_ct = q_all.all()

    total_pts = 0
    latest_by_task = {}

    for ct in all_ct:
        p = _ct_load(ct)
        st = _ct_status(ct)
        if st == "approved":
            sc = _safe_int(p.get("score_total", 0), 0)
            total_pts += sc

        tid = getattr(ct, "task_id", None)
        if tid is not None:
            prev = latest_by_task.get(tid)
            if (not prev) or _ct_get_timestamp(ct) >= _ct_get_timestamp(prev):
                latest_by_task[tid] = ct

    level = _level_of(total_pts)

    
    classmates = User.query.filter_by(
        role="student",
        unit=getattr(stu, "unit", None),
        grade=getattr(stu, "grade", None),
        class_no=getattr(stu, "class_no", None),
    ).all()

    rank = []
    weekly_class_totals = []
    for u in classmates:
        q_u = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(
            Task.task_type == "mission"
        )
        q_u = ct_user_filter(q_u, u.id)
        tot = 0
        for ct in q_u.all():
            if _ct_status(ct) == "approved":
                tot += _safe_int(_ct_load(ct).get("score_total", 0), 0)
        rank.append((tot, u))

        wpts, _ = _user_week_points(u.id, ws, we)
        weekly_class_totals.append(wpts)

    rank.sort(reverse=True, key=lambda x: x[0])
    class_size = len(rank)
    class_avg_total = round(sum(t for t, _ in rank) / class_size, 1) if class_size else 0.0
    class_week_avg = round(sum(weekly_class_totals) / len(weekly_class_totals), 1) if weekly_class_totals else 0.0

    self_rank = None
    self_total = 0
    for idx, (tot, u) in enumerate(rank, start=1):
        if u.id == stu.id:
            self_rank = idx
            self_total = tot
            break

    
    if week_goal > 0:
        if week_pts >= week_goal:
            week_msg = f"本週已達標（目標 {week_goal} 分）。"
        else:
            week_msg = f"本週還差 <b>{week_goal - week_pts}</b> 分達標，加油！"
        week_label = f"{week_pts} / {week_goal} 分"
    else:
        week_msg = "老師尚未設定本週目標，先自由累積閱讀分數吧。"
        week_label = f"{week_pts} 分（未設定目標）"

    
    m_first, m_last = _month_range(today_d)
    col = ct_time_col()
    m_ct_q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(
        Task.task_type == "mission"
    )
    m_ct_q = ct_user_filter(m_ct_q, stu.id)
    if col is not None:
        m_ct_q = m_ct_q.filter(
            func.date(col) >= m_first.isoformat(),
            func.date(col) <= m_last.isoformat(),
        )
    m_cts = m_ct_q.all()

    sdg_scores = {code: 0 for code, _ in SDG_OPTIONS}
    sdg_name_map = {code: name for code, name in SDG_OPTIONS}
    month_pts = 0

    
    coread_total = 0
    coread_week = 0

    for ct in m_cts:
        if _ct_status(ct) != "approved":
            continue
        p = _ct_load(ct)
        score = _safe_int(p.get("score_total", 0), 0)
        month_pts += score

        
        codes = []
        if p.get("sdg_codes"):
            codes = p["sdg_codes"]
        elif p.get("sdg_code"):
            codes = [p["sdg_code"]]

        used = set()
        for c in codes:
            v = _safe_int(c, 0)
            if v < 1 or v not in sdg_scores or v in used:
                continue
            used.add(v)
            sdg_scores[v] += score

        
        if reading_parent_coread_done(p):
            coread_total += 1
            
            ts = _ct_get_timestamp(ct).date()
            if ws <= ts <= we:
                coread_week += 1

    values = list(sdg_scores.values())
    month_total = month_pts

    
    if month_goal > 0:
        if month_pts >= month_goal:
            month_msg = f"本月已達標（目標 {month_goal} 分）。"
        else:
            month_msg = f"本月還差 <b>{month_goal - month_pts}</b> 分達標。"
        month_label = f"{month_pts} / {month_goal} 分"
    else:
        month_msg = "老師尚未設定本月目標，先按自己的步調累積閱讀分數。"
        month_label = f"{month_pts} 分（未設定目標）"

    
    term_pts = total_pts
    if term_goal > 0:
        if term_pts >= term_goal:
            term_msg = f"本學期已達標（目標 {term_goal} 分）。"
        else:
            term_msg = f"本學期還差 <b>{term_goal - term_pts}</b> 分達標。"
        term_label = f"{term_pts} / {term_goal} 分"
    else:
        term_msg = "老師尚未設定本學期目標，可以把目前總分當作起點繼續努力。"
        term_label = f"{term_pts} 分（未設定目標）"

    week_pct  = _pct(week_pts, week_goal)
    month_pct = _pct(month_pts, month_goal)
    term_pct  = _pct(term_pts, term_goal)

    
    if week_goal > 0:
        class_week_pct = _pct(class_week_avg, week_goal)
        if class_week_avg >= week_goal:
            class_goal_badge = "<span class='badge bg-success'>已達成本週班級目標</span>"
            class_goal_desc  = f"本班本週平均約 {class_week_avg} 分，已超過設定的 {week_goal} 分。"
        else:
            diff = round(week_goal - class_week_avg, 1)
            class_goal_badge = "<span class='badge bg-warning text-dark'>尚未達成</span>"
            class_goal_desc  = f"本班本週平均約 {class_week_avg} 分，還差約 {diff} 分達到班級目標。"
        class_goal_text = f"目標：每人 {week_goal} 分　目前平均：約 {class_week_avg} 分"
    else:
        class_week_pct = 0
        class_goal_badge = "<span class='badge bg-secondary'>未設定目標</span>"
        class_goal_desc  = f"目前班級本週平均約 {class_week_avg} 分，尚未設定班級目標。"
        class_goal_text  = f"目前平均：約 {class_week_avg} 分"

    
    nonzero_vals = [v for v in values if v > 0]
    if nonzero_vals:
        max_v = max(nonzero_vals)
        min_v = min(nonzero_vals)
        total_cats = len(SDG_OPTIONS) or 17
        coverage = len(nonzero_vals) / total_cats
        uniformity = (min_v / max_v) if max_v > 0 else 0.0
        balance_index = round(100 * coverage * uniformity, 1)
    else:
        max_v = min_v = 0
        coverage = 0.0
        uniformity = 0.0
        balance_index = 0.0

    if month_total > 0:
        balance_weight = 0.5 + balance_index / 200.0
    else:
        balance_weight = 0.5

    if month_total == 0:
        balance_status_text = "本月尚未有閱讀認證，尚未啟動加權。"
    elif balance_index >= 40.0:
        balance_status_text = (
            f"已啟動均衡加成，均衡係數約 {balance_weight:.2f}。"
            " 類別越平均、覆蓋越多，均衡指數越高。"
        )
    else:
        balance_status_text = (
            f"目前尚未達均衡門檻（均衡指數低於 40%），均衡係數約 {balance_weight:.2f}。"
            " 若多補讀較少接觸的 SDG 類別，指數會慢慢提高。"
        )

    top_label = bottom_label = recommend_label = "—"
    if any(values):
        pairs = [(code, sdg_scores[code]) for code, _ in SDG_OPTIONS]
        max_code, max_val = max(pairs, key=lambda x: x[1])
        min_code, min_val = min(pairs, key=lambda x: x[1])
        top_label = f"{max_code:02d} {sdg_name_map[max_code]}（{max_val} 分）"
        bottom_label = f"{min_code:02d} {sdg_name_map[min_code]}（{min_val} 分）"
        recommend_label = f"{min_code:02d} {sdg_name_map[min_code]}"

    sdg_chart_html = _reading_sdg_chart_html(
        sdg_scores,
        sdg_name_map,
        "本月尚未有通過的閱讀認證，完成後會產生長條圖與圓餅圖。",
    )
    active_sdg_pairs = [(code, score) for code, score in sdg_scores.items() if score > 0]
    if active_sdg_pairs:
        top_sdg_code, top_sdg_score = max(active_sdg_pairs, key=lambda item: item[1])
        zero_sdg_codes = [code for code, score in sdg_scores.items() if score == 0]
        recommend_sdg_code = zero_sdg_codes[0] if zero_sdg_codes else min(sdg_scores.items(), key=lambda item: item[1])[0]
        top_sdg_label = f"{top_sdg_code:02d} {sdg_name_map[top_sdg_code]}"
        recommend_sdg_label = f"{recommend_sdg_code:02d} {sdg_name_map[recommend_sdg_code]}"
        sdg_focus_hint = f"本月最高 {top_sdg_score} 分，可以再補強 {recommend_sdg_label}。"
    else:
        top_sdg_label = "尚未累積"
        recommend_sdg_label = "先從喜歡的主題開始"
        sdg_focus_hint = "完成閱讀認證後，這裡會出現學生的 SDG 閱讀分布。"

    rank_label = f"第 {self_rank} / {class_size} 名" if self_rank else "尚未排序"
    class_rank_hint = f"全班本週平均 {class_week_avg} 分" if class_size else "目前尚無班級閱讀資料"

    def teacher_need_parent_coread(ct, payload: dict) -> bool:
        return reading_need_parent_coread(ct=ct, payload=payload, task=getattr(ct, "task", None))

    def teacher_parent_coread_done(payload: dict) -> bool:
        return reading_parent_coread_done(payload)

    approved_record_count = 0
    pending_cnt = 0
    rejected_cnt = 0
    coread_todo = 0
    coread_done_all = 0
    last_coread_ts = None
    for ct in all_ct:
        status = _ct_status(ct)
        payload = _ct_load(ct)
        if status == "approved":
            approved_record_count += 1
        elif status == "pending":
            pending_cnt += 1
        elif status == "rejected":
            rejected_cnt += 1
        if teacher_need_parent_coread(ct, payload) and not teacher_parent_coread_done(payload) and status != "rejected":
            coread_todo += 1
        if teacher_parent_coread_done(payload):
            coread_done_all += 1
            ts = _ct_get_timestamp(ct)
            if ts and ((last_coread_ts is None) or ts > last_coread_ts):
                last_coread_ts = ts
    coread_pct = _pct(coread_done_all, approved_record_count)
    coread_label = f"{coread_done_all} / {approved_record_count} 筆" if approved_record_count else "尚無紀錄"
    last_coread_label = last_coread_ts.strftime("%Y-%m-%d") if last_coread_ts else "尚未有親子共讀"
    balance_hint = "分布逐漸平均" if balance_index >= 40 else "可以多嘗試不同 SDG 主題"

    
    if streak > 0:
        streak_msg = f"已連續 {streak} 週有閱讀認證紀錄。"
    else:
        streak_msg = "本學期尚未有連續閱讀紀錄。"

    
    col2 = ct_time_col()
    recent_q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(
        Task.task_type == "mission"
    )
    recent_q = ct_user_filter(recent_q, stu.id)
    if col2 is not None:
        recent_q = recent_q.order_by(col2.desc())
    else:
        recent_q = recent_q.order_by(CompletedTask.id.desc())
    recent_cts = recent_q.limit(8).all()

    recent_rows = []
    for ct in recent_cts:
        p = _ct_load(ct)
        status = _ct_status(ct)
        score = _safe_int(p.get("score_total", 0), 0)
        try:
            ts_str = _ct_get_timestamp(ct).strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts_str = ""
        book_title = (p.get("book_title") or "未填書名").strip()
        sdg_text = ""
        if p.get("sdg_codes"):
            codes = [str(_safe_int(c, 0)) for c in p["sdg_codes"]]
            sdg_text = "、".join([c.zfill(2) for c in codes if c])
        elif p.get("sdg_code"):
            sdg_text = str(_safe_int(p["sdg_code"], 0)).zfill(2)

        parent_tag = ""
        if reading_parent_coread_done(p):
            parent_tag = "<span class='badge bg-success ms-1'>親子共讀</span>"

        status_badge = {
            "pending": "<span class='badge bg-warning text-dark'>待審</span>",
            "approved": "<span class='badge bg-success'>已通過</span>",
            "rejected": "<span class='badge bg-danger'>退回</span>",
        }.get(status, "<span class='badge bg-secondary'>其他</span>")

        recent_rows.append(
            "<tr>"
            f"<td class='text-nowrap'>{ts_str}</td>"
            f"<td>{book_title}{parent_tag}</td>"
            f"<td class='text-nowrap'>{sdg_text or '—'}</td>"
            f"<td class='text-end'>{score}</td>"
            f"<td class='text-nowrap'>{status_badge}</td>"
            "</tr>"
        )

    if recent_rows:
        recent_table = (
            "<div class='table-responsive'>"
            "<table class='table table-sm align-middle mb-0'>"
            "<thead><tr>"
            "<th>時間</th><th>書名 / 任務</th><th>SDG 類別</th><th class='text-end'>得分</th><th>狀態</th>"
            "</tr></thead>"
            f"<tbody>{''.join(recent_rows)}</tbody></table></div>"
        )
    else:
        recent_table = "<div class='small text-muted'>尚無閱讀認證紀錄。</div>"

    latest_record_cards = []
    for ct in recent_cts[:5]:
        payload = _ct_load(ct)
        status = _ct_status(ct)
        score = _safe_int(payload.get("score_total", 0), 0)
        ts = _ct_get_timestamp(ct)
        date_text = ts.strftime("%Y-%m-%d") if ts else "未記錄日期"
        book_title = (payload.get("book_title") or getattr(getattr(ct, "task", None), "title", "") or "閱讀紀錄").strip()
        if status == "approved":
            score_text = f"{score} 分"
        elif status == "rejected":
            score_text = "未計分"
        else:
            score_text = "審核中"
        raw_codes = payload.get("sdg_codes")
        if isinstance(raw_codes, (list, tuple)):
            codes = raw_codes
        elif raw_codes:
            codes = [raw_codes]
        elif payload.get("sdg_code"):
            codes = [payload.get("sdg_code")]
        else:
            codes = []
        code_labels = []
        for raw_code in codes:
            code = _safe_int(raw_code, 0)
            if code in sdg_name_map:
                code_labels.append(f"{code:02d}")
        sdg_text = "、".join(code_labels) or "未分類"
        coread_tag = "<span class='student-history-tag'>親子共讀</span>" if reading_parent_coread_done(payload) else ""
        status_badge = {
            "pending": "<span class='badge bg-warning text-dark'>待審核</span>",
            "approved": "<span class='badge bg-success'>已通過</span>",
            "rejected": "<span class='badge bg-danger'>已退回</span>",
        }.get(status, "<span class='badge bg-secondary'>其他</span>")
        latest_record_cards.append(
            "<article class='student-history-item'>"
            "<div class='student-history-main'>"
            f"<div class='student-history-title'>《{escape(book_title)}》</div>"
            f"<div class='student-history-meta'>{date_text} · SDG {escape(sdg_text)} {coread_tag}</div>"
            "</div>"
            "<div class='student-history-side'>"
            f"{status_badge}"
            f"<div class='student-history-score'>{score_text}</div>"
            "</div>"
            "</article>"
        )
    latest_records_html = "".join(latest_record_cards) or "<div class='student-empty'>尚無閱讀紀錄，學生送出閱讀認證後會顯示在這裡。</div>"

    if getattr(current_user, "role", None) == "teacher":
        back_url = url_for("teacher") + "#teacher-reading-overview"
    else:
        back_url = url_for("reading_teacher_dashboard")

    def overview_kpi(label: str, value, sub: str = "") -> str:
        extra = f"<div class='teacher-reading-overview-kpi-sub'>{sub}</div>" if sub else ""
        return (
            "<div class='teacher-reading-overview-kpi'>"
            f"<div class='teacher-reading-overview-kpi-label'>{label}</div>"
            f"<div class='teacher-reading-overview-kpi-value'>{value}</div>"
            f"{extra}"
            "</div>"
        )

    def progress_row(label: str, percent: int, text: str) -> str:
        return (
            "<div class='teacher-reading-overview-progress-row'>"
            f"<div class='teacher-reading-overview-progress-name'>{label}</div>"
            "<div class='teacher-reading-overview-progress-track'>"
            f"<div class='teacher-reading-overview-progress-fill' style='width:{percent}%;'></div>"
            "</div>"
            f"<div class='teacher-reading-overview-progress-number'>{text}</div>"
            "</div>"
        )

    overview_records = []
    for ct in recent_cts[:5]:
        payload = _ct_load(ct)
        status = _ct_status(ct)
        score = _safe_int(payload.get("score_total", 0), 0)
        ts = _ct_get_timestamp(ct)
        date_text = ts.strftime("%Y-%m-%d") if ts else "未記錄日期"
        book_title = (payload.get("book_title") or getattr(getattr(ct, "task", None), "title", "") or "閱讀紀錄").strip()
        if status == "approved":
            score_text = f"{score} 分"
        elif status == "rejected":
            score_text = "未計分"
        else:
            score_text = "審核中"
        raw_codes = payload.get("sdg_codes")
        if isinstance(raw_codes, (list, tuple)):
            codes = raw_codes
        elif raw_codes:
            codes = [raw_codes]
        elif payload.get("sdg_code"):
            codes = [payload.get("sdg_code")]
        else:
            codes = []
        code_labels = []
        for raw_code in codes:
            code = _safe_int(raw_code, 0)
            if code in sdg_name_map:
                code_labels.append(f"{code:02d}")
        sdg_text = "、".join(code_labels) or "未分類"
        coread_tag = ""
        if teacher_need_parent_coread(ct, payload):
            coread_tag = (
                "<span class='teacher-reading-overview-tag'>親子共讀已完成</span>"
                if teacher_parent_coread_done(payload)
                else "<span class='teacher-reading-overview-tag teacher-reading-overview-tag--todo'>待補親子共讀</span>"
            )
        status_badge = {
            "pending": "<span class='badge bg-warning text-dark'>待審核</span>",
            "approved": "<span class='badge bg-success'>已通過</span>",
            "rejected": "<span class='badge bg-danger'>已退回</span>",
        }.get(status, "<span class='badge bg-secondary'>其他</span>")
        overview_records.append(
            "<article class='teacher-reading-overview-record'>"
            "<div class='teacher-reading-overview-record-main'>"
            f"<div class='teacher-reading-overview-record-title'>《{escape(book_title)}》</div>"
            f"<div class='teacher-reading-overview-record-meta'>{date_text} · SDG {escape(sdg_text)} {coread_tag}</div>"
            "</div>"
            "<div class='teacher-reading-overview-record-side'>"
            f"{status_badge}"
            f"<div class='teacher-reading-overview-record-score'>{score_text}</div>"
            "</div>"
            "</article>"
        )
    recent_overview_html = "".join(overview_records) or "<div class='teacher-reading-overview-empty'>目前尚無閱讀紀錄。</div>"

    week_range_text = f"{ws.strftime('%m/%d')} - {we.strftime('%m/%d')}"
    class_line = f"{escape(class_info)} · 本週 {week_range_text}"
    progress_html = (
        progress_row("本週", week_pct, week_label)
        + progress_row("本月", month_pct, month_label)
        + progress_row("本學期", term_pct, term_label)
    )
    observation_html = (
        "<div class='teacher-reading-overview-note-grid'>"
        f"<div><span>班級位置</span><strong>{rank_label}</strong><em>{class_rank_hint}</em></div>"
        f"<div><span>SDG 主題</span><strong>{escape(top_sdg_label)}</strong><em>{escape(sdg_focus_hint)}</em></div>"
        f"<div><span>親子共讀</span><strong>{coread_label}</strong><em>完成率 {coread_pct}% · 最近 {last_coread_label}</em></div>"
        f"<div><span>閱讀均衡</span><strong>{balance_index}%</strong><em>{balance_hint}</em></div>"
        "</div>"
    )

    html = f"""
    <div class='teacher-reading-overview-page'>
      <article class='teacher-reading-overview-card'>
        <div class='teacher-reading-overview-body'>
          <div class='teacher-reading-overview-head'>
            <div>
              <div class='teacher-reading-overview-kicker'>READING OVERVIEW</div>
              <h3>{escape(stu_name)}</h3>
              <div class='teacher-reading-overview-meta'>{class_line}</div>
            </div>
            <div class='teacher-reading-overview-actions'>
              <span class='teacher-reading-overview-pill'>親子共讀待補寫：{coread_todo}</span>
              <a class='teacher-reading-overview-back' href='{back_url}'>回閱讀概況</a>
            </div>
          </div>

          <div class='teacher-reading-overview-kpi-grid'>
            {overview_kpi("本週分數", week_pts)}
            {overview_kpi("等級", escape(str(level)), f"歷史：{total_pts} 分")}
            {overview_kpi("連續週數", streak, "每週持續閱讀累積")}
            {overview_kpi("待審核", pending_cnt, f"通過 {approved_record_count} / 退回 {rejected_cnt}")}
          </div>

          <section class='teacher-reading-overview-progress'>
            <h4>閱讀進度</h4>
            {progress_html}
          </section>

          <section class='teacher-reading-overview-section'>
            <div class='teacher-reading-overview-section-title'>
              <span>本月 SDG 圖表</span>
              <small>圓餅圖與長條圖</small>
            </div>
            {sdg_chart_html}
          </section>

          <section class='teacher-reading-overview-section'>
            <div class='teacher-reading-overview-section-title'>
              <span>老師觀察重點</span>
              <small>{escape(str(class_goal_text))}</small>
            </div>
            {observation_html}
          </section>

          <section class='teacher-reading-overview-section'>
            <div class='teacher-reading-overview-section-title'>
              <span>最近閱讀紀錄</span>
              <small>最新 5 筆</small>
            </div>
            <div class='teacher-reading-overview-record-list'>{recent_overview_html}</div>
          </section>
        </div>
      </article>
    </div>
    """
    return page("學生閱讀分析", html)

    avatar_char = escape(((stu_name or stu.username or "學").strip() or "學")[0])

    if getattr(current_user, "role", None) == "teacher":
        back_url = url_for("teacher") + "#teacher-reading-overview"
    else:
        back_url = url_for("reading_teacher_dashboard")

    def student_metric(label: str, value, sub: str = "") -> str:
        extra = f"<div class='student-metric-sub'>{sub}</div>" if sub else ""
        return (
            "<div class='student-metric'>"
            f"<div class='student-metric-value'>{value}</div>"
            f"<div class='student-metric-label'>{label}</div>"
            f"{extra}"
            "</div>"
        )

    hero = f"""
    <section class='student-hero student-reading-teacher-hero'>
      <div class='student-hero-inner'>
        <div>
          <div class='student-eyebrow'>學生閱讀檔案</div>
          <h3>{escape(stu_name)}</h3>
          <div class='student-muted mt-1'>{escape(class_info)} · 帳號 {escape(stu.username)} · {ws} ~ {we}</div>
        </div>
        <div class='student-hero-actions'>
          <a class='btn student-soft-btn' href='{back_url}'>回閱讀概況</a>
        </div>
      </div>
      <div class='student-metric-grid'>
        {student_metric("本週閱讀", week_pts, week_label)}
        {student_metric("連續週數", streak, streak_msg)}
        {student_metric("目前等級", escape(str(level)), f"累積 {total_pts} 分")}
        {student_metric("親子共讀", coread_done_all, f"完成率 {coread_pct}%")}
      </div>
    </section>
    """

    dashboard_panel = f"""
    <section id='student-dashboard-section' class='student-panel'>
      <div class='student-panel-head'>
        <div>
          <div class='student-section-kicker'>Reading</div>
          <h4>閱讀儀表板與進度</h4>
        </div>
        <span class='student-muted'>老師查看模式</span>
      </div>
      <div class='student-panel-body'>
        <div class='student-reading-card'>
          <div class='d-flex justify-content-between align-items-start gap-2 flex-wrap'>
            <div>
              <div class='student-section-kicker mb-1'>Dashboard</div>
              <div class='student-diary-title'>個別閱讀概況</div>
              <div class='student-muted'>閱讀成果、目標進度、班級位置與 SDG 分布集中在這裡。</div>
            </div>
            <div class='student-profile-avatar student-reading-mini-avatar'>{avatar_char}</div>
          </div>

          <div class='student-reading-dashboard mt-3'>
            <div class='student-reading-stat'>
              <div class='student-reading-stat-value'>{week_pts}</div>
              <div class='student-reading-stat-label'>本週分數</div>
            </div>
            <div class='student-reading-stat'>
              <div class='student-reading-stat-value'>{month_pts}</div>
              <div class='student-reading-stat-label'>本月分數</div>
            </div>
            <div class='student-reading-stat'>
              <div class='student-reading-stat-value'>{level}</div>
              <div class='student-reading-stat-label'>目前等級</div>
            </div>
            <div class='student-reading-stat'>
              <div class='student-reading-stat-value'>{total_pts}</div>
              <div class='student-reading-stat-label'>累積分數</div>
            </div>
          </div>

          <div class='student-progress-line'>
            <div class='student-progress-name'>本週</div>
            <div class='progress'><div class='progress-bar' style='width:{week_pct}%;'></div></div>
            <div class='student-progress-number'>{week_label}</div>
          </div>
          <div class='student-progress-line'>
            <div class='student-progress-name'>本月</div>
            <div class='progress'><div class='progress-bar' style='width:{month_pct}%;'></div></div>
            <div class='student-progress-number'>{month_label}</div>
          </div>
          <div class='student-progress-line'>
            <div class='student-progress-name'>本學期</div>
            <div class='progress'><div class='progress-bar' style='width:{term_pct}%;'></div></div>
            <div class='student-progress-number'>{term_label}</div>
          </div>

          <div class='student-insight-grid'>
            <div class='student-insight-card'>
              <div class='student-insight-label'>班級位置</div>
              <div class='student-insight-value'>{rank_label}</div>
              <div class='student-insight-note'>{class_rank_hint}</div>
            </div>
            <div class='student-insight-card'>
              <div class='student-insight-label'>SDG 主題</div>
              <div class='student-insight-value'>{escape(top_sdg_label)}</div>
              <div class='student-insight-note'>{escape(sdg_focus_hint)}</div>
            </div>
            <div class='student-insight-card'>
              <div class='student-insight-label'>親子共讀</div>
              <div class='student-insight-value'>{coread_label}</div>
              <div class='student-insight-note'>完成率 {coread_pct}% · 最近 {last_coread_label}</div>
            </div>
            <div class='student-insight-card'>
              <div class='student-insight-label'>閱讀均衡</div>
              <div class='student-insight-value'>{balance_index}%</div>
              <div class='student-insight-note'>{balance_hint}</div>
            </div>
          </div>

          <div class='student-reading-sections'>
            <section class='student-reading-section student-reading-chart-section'>
              <div class='student-reading-section-title'>
                <span>本月 SDG 圖表</span>
                <span class='student-muted'>建議：{escape(recommend_sdg_label)}</span>
              </div>
              {sdg_chart_html}
            </section>
            <section class='student-reading-section student-reading-history-section'>
              <div class='student-reading-section-title'>
                <span>最近閱讀紀錄</span>
                <span class='student-muted'>顯示最新 5 筆</span>
              </div>
              <div class='student-history-list'>{latest_records_html}</div>
            </section>
          </div>

          <div class='student-reading-section mt-3'>
            <div class='student-reading-section-title'>
              <span>老師觀察重點</span>
              <span class='student-muted'>{escape(str(class_goal_text))}</span>
            </div>
            <div class='student-task-list'>
              <div class='student-mini-item'><div class='fw-bold'>本週進度</div><div class='student-muted'>{week_msg}</div></div>
              <div class='student-mini-item'><div class='fw-bold'>班級比較</div><div class='student-muted'>{class_goal_badge} {class_goal_desc}</div></div>
              <div class='student-mini-item'><div class='fw-bold'>閱讀均衡</div><div class='student-muted'>{balance_status_text}</div></div>
            </div>
          </div>
        </div>
      </div>
    </section>
    """

    html = f"""
    <div class='student-page student-reading-teacher-page'>
      {hero}
      <div class='student-main-grid'>
        {dashboard_panel}
      </div>
    </div>
    """
    return page("學生閱讀分析", html)

@app.route("/announce", methods=["GET","POST"])
@roles_required("teacher","leader","admin")
def teacher_announce():
    unit, user_grade, user_class = current_user.unit, current_user.grade, current_user.class_no
    role = getattr(current_user, "role", "teacher")
    msg = ""

    
    announce_options = (
        globals().get("ANNOUNCE_CATEGORY_OPTIONS")
        or globals().get("CONDUCT_CATEGORY_OPTIONS")
        or globals().get("ANNOUNCE_OPTIONS")
        or ["一般", "秩序", "衛生", "安全", "服務", "活動", "提醒", "其他"]
    )

    
    form_title = ""
    form_category = (announce_options[0] if announce_options else "一般")
    form_desc = ""
    form_publish_date = today()  
    form_end_date = None
    form_scope = "class"  
    form_target_grade = None
    form_target_class = None

    
    _allowed_ext = {"png", "jpg", "jpeg", "gif"}
    def _ext_ok(filename: str) -> bool:
        if not filename or "." not in filename:
            return False
        return filename.rsplit(".", 1)[-1].lower() in _allowed_ext

    if request.method == "POST":
        
        title = (g("title") or "").strip()
        category = (g("category") or form_category).strip()
        desc = (g("description") or "").strip()

        
        publish_date = parse_date(g("publish_date")) or today()
        end_date = parse_date(g("end_date"))

        
        is_school_wide = 0
        target_grade = user_grade
        target_class = user_class
        scope = "class"
        if role == "leader":
            scope = (g("scope") or "class").strip()
            if scope == "school":
                is_school_wide = 1
                target_grade = None
                target_class = None
            elif scope == "grade":
                try:
                    target_grade = int(g("target_grade") or 0) or None
                except Exception:
                    target_grade = None
                target_class = None
                if not target_grade:
                    msg = "請輸入年級。"
            else:  
                try:
                    target_grade = int(g("target_grade") or 0) or None
                except Exception:
                    target_grade = None
                try:
                    target_class = int(g("target_class") or 0) or None
                except Exception:
                    target_class = None
                if not (target_grade and target_class):
                    msg = "請輸入年級與班級。"

        
        img_file = request.files.get("image")
        saved_img = None
        if img_file and img_file.filename.strip():
            if not _ext_ok(img_file.filename):
                msg = f"不支援的圖片格式：{img_file.filename}（允許：{', '.join(sorted(_allowed_ext))}）"
            else:
                saved_img = save_image(img_file)

        
        if not msg and (not title or not desc):
            msg = "請填完整標題與內容。"
        if not msg and end_date and publish_date and end_date < publish_date:
            msg = "截止日不可早於發布日。"

        if not msg:
            
            t = Task(
                title=title,
                description=desc,
                created_by=current_user.username,
                category=category,
                mission_category=None,
                points=0,
                start_date=publish_date,   
                end_date=end_date,         
                unit=unit,
                grade=target_grade,
                class_no=target_class,
                is_school_wide=is_school_wide,
                task_type="announce",      
                is_view_only=1             
            )
            _set_task_image_if_possible(t, saved_img)
            db.session.add(t)
            db.session.commit()
            return toast_redirect("teacher_announce", "已發布公告。", "success")

        
        form_title = title
        form_category = category
        form_desc = desc
        form_publish_date = publish_date or today()
        form_end_date = end_date
        form_scope = scope
        form_target_grade = target_grade
        form_target_class = target_class

    
    if role == "leader":
        
        ann_list = (Task.query
                    .filter(Task.unit == unit, Task.task_type == "announce",
                            Task.created_by == current_user.username)
                    .order_by(Task.start_date.desc(), Task.id.desc())
                    .all())
    else:
        
        ann_list = (Task.query
                    .filter_by(unit=unit, grade=user_grade, class_no=user_class, task_type="announce")
                    .order_by(Task.start_date.desc(), Task.id.desc())
                    .all())

    
    def _render_cat_options(selected: str) -> str:
        opts = []
        seen = set()
        for c in announce_options:
            seen.add(c)
            sel = " selected" if (selected == c) else ""
            opts.append(f"<option value='{c}'{sel}>{c}</option>")
        if selected and selected not in seen:
            
            opts.insert(0, f"<option value='{selected}' selected>{selected}（自訂）</option>")
        return "".join(opts)

    cat_opts = _render_cat_options(form_category)

    
    scope_fields = ""
    if role == "leader":
        def _val(v): return "" if v is None else str(v)
        def _sel(x, y): return " selected" if x == y else ""
        scope_fields = (
            "<div class='col-md-4'>"
            "<label class='form-label'>對象</label>"
            f"<select name='scope' id='ann_scope' class='form-select'>"
            f"<option value='class'{_sel(form_scope,'class')}>特定班級</option>"
            f"<option value='grade'{_sel(form_scope,'grade')}>年級</option>"
            f"<option value='school'{_sel(form_scope,'school')}>全校</option>"
            "</select>"
            "</div>"
            f"<div class='col-md-2 scope-grade' style='display:{'none' if form_scope=='school' else 'block'}'>"
            "<label class='form-label'>年級</label>"
            f"<input type='number' min='1' max='99' name='target_grade' class='form-control' placeholder='例：5' value='{_val(form_target_grade)}'>"
            "</div>"
            f"<div class='col-md-2 scope-class' style='display:{'block' if form_scope=='class' else 'none'}'>"
            "<label class='form-label'>班級</label>"
            f"<input type='number' min='1' max='99' name='target_class' class='form-control' placeholder='例：3' value='{_val(form_target_class)}'>"
            "</div>"
            "<script>"
            "const asc=document.getElementById('ann_scope');"
            "function toggleAnnScope(){"
            "  const v=asc.value;"
            "  document.querySelectorAll('.scope-grade').forEach(e=>e.style.display=(v==='school')?'none':'block');"
            "  document.querySelectorAll('.scope-class').forEach(e=>e.style.display=(v==='class')?'block':'none');"
            "}"
            "asc.addEventListener('change',toggleAnnScope);"
            "document.addEventListener('DOMContentLoaded',toggleAnnScope);"
            "</script>"
        )

    _today_iso = (form_publish_date or today()).isoformat()
    _end_iso = (form_end_date.isoformat() if form_end_date else "")

    form = (
        "<div class='card border-0 shadow-sm mb-4 announce-form-panel'><div class='card-body'>"
        "<div class='announce-panel-head'>"
        f"<div><div class='announce-kicker'>Publish</div><h5 class='card-title mb-1'>{'組長' if role=='leader' else '老師'}發布公告</h5>"
        "<div class='announce-muted'>公告會出現在學生與家長的今日聯絡簿中。</div></div>"
        "</div>"
        f"{('<div class=\"alert alert-warning mb-3\">'+msg+'</div>') if msg else ''}"
        "<form method='post' class='row g-3' enctype='multipart/form-data'>"
        f"<div class='col-md-4'><label class='form-label'>標題</label><input name='title' class='form-control' required value=\"{escape(form_title)}\"></div>"
        f"<div class='col-md-4'><label class='form-label'>分類</label><select name='category' class='form-select'>{cat_opts}</select></div>"
        f"{scope_fields}"
        f"<div class='col-md-4'><label class='form-label'>發布日</label><input type='date' name='publish_date' value='{_today_iso}' class='form-control' required></div>"
        f"<div class='col-md-4'><label class='form-label'>截止日（可留空）</label><input type='date' name='end_date' value='{_end_iso}' class='form-control'></div>"
        "<div class='col-md-4'><label class='form-label'>附圖（png/jpg/jpeg/gif，可留空）</label><input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"
        f"<div class='col-12'><label class='form-label'>內容</label><textarea name='description' class='form-control' rows='4' required>{escape(form_desc)}</textarea></div>"
        "<div class='col-12'><button class='btn btn-success'>發布</button></div>"
        "</form></div></div>"
    )

    
    def row(t: "Task"):
        deadline = t.end_date or "—"
        img_name = task_img_name(t)
        img = img_html(img_name, maxw=280) if img_name else ""

        is_new = (t.start_date and (today() - t.start_date).days <= 3)
        new_badge = " <span class='badge bg-danger ms-1'>NEW</span>" if is_new else ""
        expired = (t.end_date and today() > t.end_date)
        card_cls = "opacity-75" if expired else ""

        tools = (
            f"<a class='btn announce-soft-btn' href='/edit_task/{t.id}'>編輯</a>"
            f"<a class='btn announce-soft-btn announce-soft-btn--danger' href='/delete_task/{t.id}' onclick='return confirm(\"刪除此公告？\");'>刪除</a>"
        )
        scope_badge = f"<span class='announce-tag'>{_scope_label(t)}</span>"
        expired_tag = "<span class='announce-tag announce-tag--muted'>已過期</span>" if expired else ""
        return (
            f"<article class='announce-card {card_cls}'>"
            "<div class='announce-card-main'>"
            "<div class='announce-card-top'>"
            f"<div><div class='announce-card-title'>{escape(t.title)} {new_badge}</div>"
            f"<div class='announce-card-meta'>{escape(str(t.start_date))} 發布 · 截止 {escape(str(deadline))} · {escape(display_name_of(t.created_by))}</div></div>"
            f"<div class='announce-tags'><span>{escape(t.category or '一般')}</span>{scope_badge}{expired_tag}</div>"
            "</div>"
            f"<div class='announce-card-content'>{_desc_clean_html(t.description or '')}</div>"
            f"{img}"
            "</div>"
            f"<div class='announce-card-actions'>{tools}</div>"
            "</article>"
        )

    body = "".join(row(t) for t in ann_list) or "<div class='announce-empty'>目前尚無公告。</div>"
    table = (
        "<section class='card border-0 shadow-sm announce-list-panel'><div class='card-body'>"
        "<div class='announce-panel-head'>"
        "<div><div class='announce-kicker'>Records</div><h5 class='card-title mb-1'>公告列表</h5>"
        "<div class='announce-muted'>依發布日期排序，最新公告會優先顯示。</div></div>"
        f"<span class='badge bg-light text-muted border align-self-center'>共 {len(ann_list)} 則</span>"
        "</div>"
        f"<div class='announce-list'>{body}</div></div></section>"
    )

    active_count = sum(1 for t in ann_list if not t.end_date or today() <= t.end_date)
    with_image_count = sum(1 for t in ann_list if task_img_name(t))
    school_count = sum(1 for t in ann_list if getattr(t, "is_school_wide", 0))
    announce_style = """
    <style>
    .announce-page{display:flex;flex-direction:column;gap:1rem;}
    .announce-hero{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;border-radius:30px;padding:1.35rem;background:linear-gradient(135deg,#ecfdf3 0%,#fff8e8 58%,#eef7ff 100%);border:1px solid rgba(35,92,59,.1);box-shadow:0 24px 60px rgba(24,76,46,.12);}
    .announce-kicker{font-size:.78rem;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#4f8f61;margin-bottom:.25rem;}
    .announce-hero h3{margin:0;color:#162318;font-weight:950;letter-spacing:-.02em;}
    .announce-muted{color:#647067;font-size:.94rem;line-height:1.65;}
    .announce-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;}
    .announce-summary>div{border-radius:20px;background:#fff;border:1px solid rgba(19,56,35,.08);box-shadow:0 12px 28px rgba(24,76,46,.06);padding:.9rem 1rem;}
    .announce-summary span{display:block;color:#718078;font-size:.82rem;font-weight:900;}
    .announce-summary strong{display:block;color:#183d28;font-size:1.5rem;line-height:1.1;font-weight:950;margin-top:.18rem;}
    .announce-form-panel,.announce-list-panel{border-radius:28px!important;overflow:hidden;}
    .announce-panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;margin-bottom:1rem;}
    .announce-panel-head h5{font-weight:950;color:#17231b;}
    .announce-list{display:flex;flex-direction:column;gap:.75rem;}
    .announce-card{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;border-radius:22px;background:linear-gradient(180deg,#fff 0%,#fbfff8 100%);border:1px solid rgba(19,56,35,.08);box-shadow:0 14px 30px rgba(24,76,46,.07);padding:1rem;}
    .announce-card-main{min-width:0;flex:1;}
    .announce-card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:.8rem;flex-wrap:wrap;}
    .announce-card-title{font-size:1.08rem;font-weight:950;color:#17231b;}
    .announce-card-meta{color:#647067;font-size:.9rem;font-weight:800;margin-top:.18rem;}
    .announce-card-content{margin-top:.75rem;border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:.85rem 1rem;color:#24362c;line-height:1.8;}
    .announce-tags{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:flex-end;}
    .announce-tags span,.announce-tag{display:inline-flex;border-radius:999px;background:#f4faf5;border:1px solid rgba(35,92,59,.1);color:#2f7446;font-size:.78rem;font-weight:850;padding:.2rem .5rem;}
    .announce-tag--muted{background:#f8fafc;color:#718078;}
    .announce-card-actions{display:flex;gap:.45rem;align-items:center;justify-content:flex-end;}
    .announce-soft-btn{border-radius:999px!important;border:1px solid rgba(35,92,59,.18)!important;background:#fff!important;color:#244c32!important;font-weight:850!important;padding:.42rem .82rem!important;box-shadow:0 10px 24px rgba(24,76,46,.08);}
    .announce-soft-btn--danger{color:#a33a3a!important;border-color:rgba(163,58,58,.22)!important;}
    .announce-empty{border-radius:20px;background:#f8fbf6;border:1px dashed rgba(35,92,59,.16);color:#718078;padding:1rem;}
    @media (max-width:991.98px){.announce-summary{grid-template-columns:repeat(2,minmax(0,1fr));}}
    @media (max-width:767.98px){.announce-hero,.announce-form-panel,.announce-list-panel{border-radius:22px!important;}.announce-summary{grid-template-columns:1fr;}.announce-card-actions{justify-content:flex-start;width:100%;}.announce-tags{justify-content:flex-start;}}
    </style>
    """
    head = (
        announce_style
        + "<div class='announce-page'>"
        + "<section class='announce-hero'>"
        + f"<div><div class='announce-kicker'>Announcement</div><h3>公告管理</h3><div class='announce-muted mt-1'>{escape(unit or '')} · {escape(str(user_grade or ''))}年{escape(str(user_class or ''))}班</div></div>"
        + f"<a class='btn announce-soft-btn' href='{url_for(role_endpoint(role))}'>返回工作區</a>"
        + "</section>"
        + "<div class='announce-summary'>"
        + f"<div><span>公告總數</span><strong>{len(ann_list)}</strong></div>"
        + f"<div><span>進行中</span><strong>{active_count}</strong></div>"
        + f"<div><span>含附圖</span><strong>{with_image_count}</strong></div>"
        + f"<div><span>全校公告</span><strong>{school_count}</strong></div>"
        + "</div>"
    )
    return page("公告發布", head + form + table + "</div>", msg=msg)

@app.route("/admin_task_delete/<int:task_id>")
@roles_required("admin")
def admin_task_delete(task_id):
    abort(404)
    t=Task.query.get_or_404(task_id)
    CompletedTask.query.filter_by(task_id=task_id).delete()
    db.session.delete(t); db.session.commit()
    return toast_redirect("admin_tasks", "任務已刪除。", "success")

def _record_text(text_value: str | None) -> str:
    return _user_text_html(text_value)


def _status_badge_map(value: str | None, labels: dict[str, str]) -> str:
    value = (value or "").strip()
    label = labels.get(value, value or "未設定")
    cls = {
        "pending": "bg-warning text-dark",
        "done": "bg-success",
        "skipped": "bg-secondary",
        "open": "bg-warning text-dark",
        "followed": "bg-primary",
        "closed": "bg-success",
    }.get(value, "bg-secondary")
    return f"<span class='badge {cls}'>{label}</span>"


def _managed_students_for_records(user=None) -> list["User"]:
    u = user or current_user
    role = getattr(u, "role", None)
    if role in ("leader", "admin"):
        return (
            User.query.filter_by(role="student", unit=getattr(u, "unit", None) or SCHOOL_NAME)
            .order_by(User.grade, User.class_no, User.username)
            .all()
        )
    if role == "teacher" and is_homeroom_of(u, getattr(u, "grade", ""), getattr(u, "class_no", "")):
        return (
            User.query.filter_by(role="student", unit=u.unit, grade=u.grade, class_no=u.class_no)
            .order_by(User.username.asc())
            .all()
        )
    return []


def _parent_students_for_records(user=None) -> list["User"]:
    out = []
    for link in parent_child_links(user):
        stu = User.query.filter_by(username=link.student_name, role="student").first()
        if stu:
            out.append(stu)
    return out


def _students_for_service_record_create(user=None) -> list["User"]:
    u = user or current_user
    role = getattr(u, "role", None)
    if role == "parent":
        return _parent_students_for_records(u)
    if role in ("teacher", "leader", "admin"):
        return _managed_students_for_records(u)
    return []


def _student_for_service_record_create(username: str, user=None):
    if not username:
        return None
    return next(
        (s for s in _students_for_service_record_create(user) if s.username == username),
        None,
    )


def _service_creator_label(role: str | None) -> str:
    return {
        "parent": "家長",
        "teacher": "老師",
        "leader": "組長",
        "admin": "管理員",
        "student": "學生",
    }.get(role or "", "建立者")


def _student_select_options(students: list["User"]) -> str:
    return "".join(
        f"<option value='{s.username}'>{s.display_name or s.username}（{s.grade}年{s.class_no}班）</option>"
        for s in students
    ) or "<option value=''>（目前沒有可選學生）</option>"


def _can_manage_record_class(user, grade, class_no) -> bool:
    if not user:
        return False
    if getattr(user, "role", None) in ("leader", "admin"):
        return True
    return is_homeroom_of(user, grade, class_no)


def _student_for_bound_parent(username: str):
    if not username:
        return None
    if not parent_child_query(student=username).first():
        return None
    return User.query.filter_by(username=username, role="student").first()


@app.route("/medication", methods=["GET", "POST"])
@login_required
def medication_records():
    dsel = parse_date((request.values.get("date") or "").strip()) or today()
    role = current_user.role

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "create":
            stu = _student_for_service_record_create((request.form.get("student") or "").strip())
            medicine_name = (request.form.get("medicine_name") or "").strip()
            dose = (request.form.get("dose") or "").strip()
            time_note = (request.form.get("time_note") or "").strip()
            note = (request.form.get("note") or "").strip()
            rec_date = parse_date(request.form.get("date")) or dsel
            if not stu:
                return toast_redirect("medication_records", "請選擇可新增紀錄的學生。", "warning", date=dsel.isoformat())
            if not medicine_name:
                return toast_redirect("medication_records", "請填寫藥品名稱。", "warning", date=rec_date.isoformat())
            db.session.add(MedicationRecord(
                date=rec_date,
                student_name=stu.username,
                student_display=stu.display_name or stu.username,
                unit=stu.unit,
                grade=stu.grade,
                class_no=stu.class_no,
                medicine_name=medicine_name,
                dose=dose,
                time_note=time_note,
                note=note,
                status="pending",
                created_by=current_user.username,
                created_role=role,
            ))
            db.session.commit()
            return toast_redirect("medication_records", "已新增用藥紀錄。", "success", date=rec_date.isoformat())

        if action == "update":
            rec = MedicationRecord.query.get(request.form.get("rid"))
            if not rec or not _can_manage_record_class(current_user, rec.grade, rec.class_no):
                return toast_redirect("medication_records", "無權限更新此用藥紀錄。", "warning", date=dsel.isoformat())
            status = (request.form.get("status") or "").strip()
            if status not in MEDICATION_STATUS_OPTIONS:
                status = "pending"
            rec.status = status
            rec.teacher_note = (request.form.get("teacher_note") or "").strip()
            rec.handled_by = current_user.username
            rec.handled_at = datetime.utcnow()
            db.session.commit()
            return toast_redirect("medication_records", "已更新用藥處理狀態。", "success", date=rec.date.isoformat())

        if action == "delete":
            rec = MedicationRecord.query.get(request.form.get("rid"))
            can_delete = bool(
                rec and (
                    _can_manage_record_class(current_user, rec.grade, rec.class_no)
                    or (role == "parent" and rec.created_by == current_user.username and rec.status == "pending")
                )
            )
            if not can_delete:
                return toast_redirect("medication_records", "無權限刪除此用藥紀錄。", "warning", date=dsel.isoformat())
            rec_date = rec.date
            db.session.delete(rec)
            db.session.commit()
            return toast_redirect("medication_records", "已刪除用藥紀錄。", "success", date=rec_date.isoformat())

    q = MedicationRecord.query.filter(MedicationRecord.date == dsel)
    if role == "parent":
        kids = parent_child_student_usernames()
        q = q.filter(MedicationRecord.student_name.in_(kids)) if kids else q.filter(text("0=1"))
    elif role == "student":
        q = q.filter(MedicationRecord.student_name == current_user.username)
    elif role == "teacher":
        if not is_homeroom_of(current_user, getattr(current_user, "grade", ""), getattr(current_user, "class_no", "")):
            q = q.filter(text("0=1"))
        else:
            q = q.filter(
                MedicationRecord.unit == current_user.unit,
                MedicationRecord.grade == current_user.grade,
                MedicationRecord.class_no == current_user.class_no,
            )
    elif role in ("leader", "admin"):
        q = q.filter(MedicationRecord.unit == (current_user.unit or SCHOOL_NAME))
    else:
        return toast_redirect(role_endpoint(role), "無權限查看用藥紀錄。", "warning")

    rows = q.order_by(MedicationRecord.grade, MedicationRecord.class_no, MedicationRecord.student_name, MedicationRecord.id.desc()).all()
    pending_count = sum(1 for r in rows if r.status == "pending")
    done_count = sum(1 for r in rows if r.status == "done")
    skipped_count = sum(1 for r in rows if r.status == "skipped")

    hero_html = f"""
    <section class='med-hero'>
      <div>
        <div class='med-eyebrow'>用藥管理</div>
        <h2>用藥紀錄</h2>
      </div>
      <div class='med-date-card'>
        <span>{dsel.strftime('%Y')}</span>
        <strong>{dsel.strftime('%m/%d')}</strong>
        <a href='{url_for("medication_records", date=today().isoformat())}'>回到今天</a>
      </div>
    </section>
    <section class='med-nav'>
      <a class='btn btn-sm btn-outline-secondary' href='{url_for("medication_records", date=(dsel - timedelta(days=1)).isoformat())}'>&laquo; 前一天</a>
      <form method='get' class='med-date-form'>
        <input type='date' name='date' value='{dsel.isoformat()}' class='form-control form-control-sm'>
        <button class='btn btn-sm btn-primary'>前往</button>
      </form>
      <a class='btn btn-sm btn-outline-secondary' href='{url_for("medication_records", date=(dsel + timedelta(days=1)).isoformat())}'>下一天 &raquo;</a>
    </section>
    <section class='med-summary'>
      <div><strong>{len(rows)}</strong><span>今日紀錄</span></div>
      <div><strong>{pending_count}</strong><span>待處理</span></div>
      <div><strong>{done_count}</strong><span>已處理</span></div>
      <div><strong>{skipped_count}</strong><span>未用藥</span></div>
    </section>
    """

    create_html = ""
    if role in ("parent", "teacher", "leader", "admin"):
        create_students = _students_for_service_record_create()
        form_label = "家長填寫" if role == "parent" else "校務填寫"
        form_title = "新增今日用藥需求" if role == "parent" else "新增用藥紀錄"
        form_tag = "用藥資訊" if role == "parent" else "校務紀錄"
        submit_disabled = " disabled" if not create_students else ""
        create_html = f"""
        <section class='med-panel med-panel--form'>
          <div class='med-panel__head'>
            <div>
              <div class='med-section-label'>{form_label}</div>
              <h3>{form_title}</h3>
            </div>
            <span class='med-soft-tag'>{form_tag}</span>
          </div>
          <form method='post' class='row g-3'>
            <input type='hidden' name='action' value='create'>
            <div class='col-md-3'><label class='form-label'>日期</label><input type='date' name='date' class='form-control' value='{dsel.isoformat()}' required></div>
            <div class='col-md-5'><label class='form-label'>學生</label><select name='student' class='form-select' required>{_student_select_options(create_students)}</select></div>
            <div class='col-md-4'><label class='form-label'>藥品名稱</label><input name='medicine_name' class='form-control' placeholder='例：感冒藥、過敏藥' required></div>
            <div class='col-md-4'><label class='form-label'>劑量 / 方式</label><input name='dose' class='form-control' placeholder='例：半包、1 顆、飯後'></div>
            <div class='col-md-4'><label class='form-label'>用藥時間</label><input name='time_note' class='form-control' placeholder='例：午餐後、12:30'></div>
            <div class='col-md-4'><label class='form-label'>處理狀態</label><input class='form-control' value='待處理' disabled></div>
            <div class='col-12'><label class='form-label'>注意事項</label><textarea name='note' class='form-control' rows='3' placeholder='請補充保存方式、過敏提醒、是否需冷藏或其他注意事項。'></textarea></div>
            <div class='col-12 text-end'><button class='btn btn-primary'{submit_disabled}>送出用藥紀錄</button></div>
          </form>
        </section>
        """

    def med_card(rec: "MedicationRecord") -> str:
        status_opts = "".join(
            f"<option value='{k}' {'selected' if rec.status == k else ''}>{v}</option>"
            for k, v in MEDICATION_STATUS_OPTIONS.items()
        )
        teacher_form = ""
        if _can_manage_record_class(current_user, rec.grade, rec.class_no):
            teacher_form = f"""
            <form method='post' class='med-update-form'>
              <input type='hidden' name='action' value='update'>
              <input type='hidden' name='rid' value='{rec.id}'>
              <select name='status' class='form-select form-select-sm'>{status_opts}</select>
              <input name='teacher_note' class='form-control form-control-sm' value='{escape(rec.teacher_note or "")}' placeholder='處理備註，例如：已於午餐後服用'>
              <button class='btn btn-sm btn-outline-primary'>更新狀態</button>
            </form>
            """
        delete_btn = ""
        if _can_manage_record_class(current_user, rec.grade, rec.class_no) or (role == "parent" and rec.created_by == current_user.username and rec.status == "pending"):
            delete_btn = f"""
            <form method='post' class='d-inline' onsubmit="return confirm('確定刪除此用藥紀錄？');">
              <input type='hidden' name='action' value='delete'>
              <input type='hidden' name='rid' value='{rec.id}'>
              <button class='btn btn-sm btn-outline-danger'>刪除</button>
            </form>
            """
        created_txt = rec.created_at.strftime("%H:%M") if rec.created_at else "—"
        handled_txt = rec.handled_at.strftime("%H:%M") if rec.handled_at else ""
        dose_txt = escape(rec.dose or "未填")
        time_txt = escape(rec.time_note or "未指定")
        note_html = f"<div class='med-note'>{_record_text(rec.note)}</div>" if rec.note else ""
        teacher_note_html = f"<div class='med-teacher-note'>老師備註：{_record_text(rec.teacher_note)}</div>" if rec.teacher_note else ""
        creator_label = _service_creator_label(rec.created_role)
        creator_name = display_name_of(rec.created_by) or "—"
        return f"""
        <article class='med-card'>
          <div class='med-card__main'>
            <div class='med-card__top'>
              <div>
                <div class='med-student'>{rec.student_display or rec.student_name}</div>
                <div class='med-meta'>{rec.grade}年{rec.class_no}班 · 建立者：{creator_name}（{creator_label}） · 建立 {created_txt}</div>
              </div>
              <div class='med-card__status'>{_status_badge_map(rec.status, MEDICATION_STATUS_OPTIONS)}</div>
            </div>
            <div class='med-medicine'>{escape(rec.medicine_name)}</div>
            <div class='med-detail-grid'>
              <div><span>劑量 / 方式</span><strong>{dose_txt}</strong></div>
              <div><span>用藥時間</span><strong>{time_txt}</strong></div>
              <div><span>處理時間</span><strong>{handled_txt or "尚未處理"}</strong></div>
            </div>
            {note_html}
            {teacher_note_html}
            {teacher_form}
          </div>
          <div class='med-card__actions'>{delete_btn}</div>
        </article>
        """

    records_body = (
        "<div class='med-list'>" + "".join(med_card(r) for r in rows) + "</div>"
        if rows else
        "<div class='med-empty'><div class='med-empty__title'>這一天沒有用藥紀錄</div></div>"
    )
    records_title = "需要注意的用藥紀錄" if rows else "今日用藥狀態"
    list_html = f"""
    <section class='med-panel med-panel--records'>
      <div class='med-panel__head'>
        <div>
          <div class='med-section-label'>今日狀態</div>
          <h3>{records_title}</h3>
        </div>
        <span class='med-soft-tag'>{len(rows)} 筆</span>
      </div>
      {records_body}
    </section>
    """
    return page("用藥紀錄", f"<div class='med-page'>{hero_html}{list_html}{create_html}</div>")


@app.route("/care", methods=["GET", "POST"])
@login_required
def care_records():
    return toast_redirect(role_endpoint(current_user.role), "此項目目前未開放。", "info")

def _leave_status_badge(value) -> str:
    if value is None:
        return "<span class='badge bg-warning text-dark'>待審核</span>"
    if value == 1:
        return "<span class='badge bg-success'>已核准</span>"
    return "<span class='badge bg-danger'>已駁回</span>"

def _leave_status_text(value) -> str:
    if value is None:
        return "待審核"
    if value == 1:
        return "已核准"
    return "已駁回"

def _leave_is_homeroom_teacher(user=None) -> bool:
    u = user or current_user
    return (
        getattr(u, "role", None) == "teacher"
        and str(getattr(u, "is_homeroom", 0)) == "1"
        and bool(getattr(u, "grade", None))
        and bool(getattr(u, "class_no", None))
    )

def _can_review_leave_request(lr: "LeaveRequest", user=None) -> bool:
    u = user or current_user
    if not lr or not u:
        return False
    if getattr(u, "role", None) in ("leader", "admin"):
        return True
    return (
        _leave_is_homeroom_teacher(u)
        and str(lr.grade) == str(getattr(u, "grade", ""))
        and str(lr.class_no) == str(getattr(u, "class_no", ""))
    )

def _can_delete_leave_request(lr: "LeaveRequest", user=None) -> bool:
    u = user or current_user
    if not lr or not u:
        return False
    if getattr(u, "role", None) in ("leader", "admin"):
        return True
    return _can_review_leave_request(lr, u)

def _apply_leave_review_action(lr: "LeaveRequest", action: str, note: str = "") -> tuple[bool, str]:
    if not _can_review_leave_request(lr):
        return False, "無權操作這筆請假。"

    note = (note or "").strip()
    if action == "approve":
        lr.status = 1
        lr.review_note = note or None
        lr.reviewed_by = current_user.username
        lr.reviewed_at = datetime.utcnow()
        db.session.add(lr)
        return True, "已核准這筆請假。"

    if action == "reject":
        if not note:
            return False, "請輸入駁回理由。"
        lr.status = 0
        lr.review_note = note
        lr.reviewed_by = current_user.username
        lr.reviewed_at = datetime.utcnow()
        db.session.add(lr)
        return True, "已駁回這筆請假。"

    if action == "delete":
        if not _can_delete_leave_request(lr):
            return False, "無權刪除這筆請假。"
        db.session.delete(lr)
        return True, "已刪除這筆請假。"

    return False, "未知的請假操作。"

def _leave_ui_style() -> str:
    return """
    <style>
    .leave-page{display:flex;flex-direction:column;gap:1rem;}
    .leave-hero{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;border-radius:30px;padding:1.35rem;background:linear-gradient(135deg,#ecfdf3 0%,#fff8e8 58%,#eef7ff 100%);border:1px solid rgba(35,92,59,.1);box-shadow:0 24px 60px rgba(24,76,46,.12);}
    .leave-kicker{font-size:.78rem;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#4f8f61;margin-bottom:.25rem;}
    .leave-hero h3{margin:0;color:#162318;font-weight:950;letter-spacing:-.02em;}
    .leave-muted{color:#647067;font-size:.94rem;line-height:1.65;}
    .leave-hero-actions,.leave-date-nav,.leave-card-actions,.leave-form-actions{display:flex;gap:.55rem;align-items:center;flex-wrap:wrap;}
    .leave-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;}
    .leave-summary>div{border-radius:20px;background:#fff;border:1px solid rgba(19,56,35,.08);box-shadow:0 12px 28px rgba(24,76,46,.06);padding:.9rem 1rem;}
    .leave-summary span{display:block;color:#718078;font-size:.82rem;font-weight:900;}
    .leave-summary strong{display:block;color:#183d28;font-size:1.5rem;line-height:1.1;font-weight:950;margin-top:.18rem;}
    .leave-flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.75rem;}
    .leave-flow>div{border-radius:20px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:1rem;box-shadow:0 12px 28px rgba(24,76,46,.05);}
    .leave-flow strong{display:block;color:#183d28;font-weight:950;}
    .leave-flow span{display:block;color:#647067;font-size:.92rem;line-height:1.55;margin-top:.25rem;}
    .leave-panel{border-radius:28px!important;overflow:hidden;}
    .leave-panel .card-body{padding:1.15rem 1.25rem;}
    .leave-panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;margin-bottom:1rem;}
    .leave-panel-head h4,.leave-panel-head h5{margin:0;color:#17231b;font-weight:950;}
    .leave-card-list{display:flex;flex-direction:column;gap:.75rem;}
    .leave-card{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;border-radius:22px;background:linear-gradient(180deg,#fff 0%,#fbfff8 100%);border:1px solid rgba(19,56,35,.08);box-shadow:0 14px 30px rgba(24,76,46,.07);padding:1rem;}
    .leave-card-main{min-width:0;flex:1;}
    .leave-card-title{font-size:1.08rem;font-weight:950;color:#17231b;}
    .leave-card-meta{color:#647067;font-size:.9rem;font-weight:800;margin-top:.18rem;}
    .leave-card-reason,.leave-review-note{margin-top:.7rem;border-radius:16px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:.75rem .85rem;color:#24362c;line-height:1.7;}
    .leave-review-form{min-width:17rem;display:flex;flex-direction:column;gap:.55rem;align-items:stretch;}
    .leave-review-form textarea{min-height:4.4rem;}
    .leave-detail-link{display:inline-flex;align-items:center;justify-content:center;border-radius:999px;border:1px solid rgba(35,92,59,.16);background:#fff;color:#244c32;font-weight:900;text-decoration:none;padding:.32rem .72rem;}
    .leave-detail-link:hover{background:#f3fbf5;color:#16351f;}
    .leave-detail-grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(21rem,.95fr);gap:1rem;align-items:start;}
    .leave-detail-panel{border-radius:28px;background:#fff;border:1px solid rgba(19,56,35,.08);box-shadow:0 22px 54px rgba(24,76,46,.1);padding:1.15rem;min-width:0;}
    .leave-detail-panel--sticky{position:sticky;top:.8rem;}
    .leave-detail-head{display:flex;justify-content:space-between;align-items:flex-start;gap:.8rem;flex-wrap:wrap;margin-bottom:.9rem;}
    .leave-detail-head h4{margin:.12rem 0 0;color:#17231b;font-weight:950;}
    .leave-detail-box{border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;color:#24362c;line-height:1.85;}
    .leave-detail-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem;margin-bottom:.9rem;}
    .leave-detail-meta>div{border-radius:16px;background:#fbfff8;border:1px solid rgba(19,56,35,.08);padding:.75rem .85rem;}
    .leave-detail-meta span{display:block;color:#718078;font-size:.82rem;font-weight:900;}
    .leave-detail-meta strong{display:block;color:#183d28;font-size:1.05rem;font-weight:950;margin-top:.15rem;}
    .leave-detail-form{border-radius:20px;background:#fbfdf8;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .leave-date-form{display:flex;gap:.45rem;align-items:center;}
    .leave-date-form .form-control{border-radius:999px;min-width:10rem;}
    .leave-empty{border-radius:20px;background:#f8fbf6;border:1px dashed rgba(35,92,59,.16);color:#718078;padding:1rem;}
    @media (max-width:991.98px){.leave-summary,.leave-flow{grid-template-columns:repeat(2,minmax(0,1fr));}.leave-review-form{width:100%;}.leave-detail-grid{grid-template-columns:1fr;}.leave-detail-panel--sticky{position:static;}}
    @media (max-width:767.98px){.leave-hero,.leave-panel,.leave-detail-panel{border-radius:22px!important;}.leave-summary,.leave-flow,.leave-detail-meta{grid-template-columns:1fr;}.leave-card-actions,.leave-hero-actions,.leave-date-nav{justify-content:flex-start;}.leave-date-form{width:100%;}.leave-date-form .form-control{flex:1;min-width:0;}}
    </style>
    """

@app.route("/leave", methods=["GET","POST"])
@login_required
def leave_apply():
    msg = ""
    role = current_user.role
    if role not in ("parent", "teacher", "leader", "admin"):
        return toast_redirect(role_endpoint(role), "無權限新增請假紀錄。", "warning")

    students = _students_for_service_record_create()

    if request.method == "POST":
        stu_username = (request.form.get("student") or "").strip()
        ltype = (request.form.get("leave_type") or "").strip()
        reason = (request.form.get("reason") or "").strip()
        sd = request.form.get("start_date") or ""
        ed = request.form.get("end_date") or ""

        if not (stu_username and ltype and sd and ed):
            msg = "請完整填寫學生、假別、起迄日期。"
        elif ltype not in LEAVE_TYPES:
            msg = "假別不在允許清單中。"
        else:
            try:
                sd_date = datetime.strptime(sd, "%Y-%m-%d").date()
                ed_date = datetime.strptime(ed, "%Y-%m-%d").date()
            except Exception:
                sd_date = None
                ed_date = None
            if not sd_date or not ed_date or ed_date < sd_date:
                msg = "日期區間不正確。"
            else:
                s = next((x for x in students if x.username == stu_username), None)
                if not s:
                    msg = "這位學生不在可新增的範圍內。"
                else:
                    lr = LeaveRequest(
                        applicant_name=current_user.username,
                        applicant_role=role,
                        student_name=s.username,
                        student_display=s.display_name or s.username,
                        unit=s.unit, grade=s.grade, class_no=s.class_no,
                        leave_type=ltype, reason=reason,
                        start_date=sd_date, end_date=ed_date,
                        status=None
                    )
                    db.session.add(lr)
                    db.session.commit()
                    if role == "parent":
                        return toast_redirect("leave_mine", "已送出請假申請。", "success")
                    return toast_redirect("leave_manage", "已新增請假紀錄。", "success", date=sd_date.isoformat())

    stu_opts_html = "".join(
        f"<option value='{s.username}'>{(s.display_name or s.username)}（{s.grade}年{s.class_no}班）</option>"
        for s in students
    ) or "<option value=''>（尚無可申請的學生）</option>"
    leave_opts_html = "".join(f"<option value='{t}'>{t}</option>" for t in LEAVE_TYPES)
    today_str = today().isoformat()

    child_usernames = [s.username for s in students]
    recent_rows = (
        LeaveRequest.query
        .filter(LeaveRequest.student_name.in_(child_usernames))
        .order_by(LeaveRequest.created_at.desc())
        .limit(3)
        .all()
        if child_usernames else []
    )
    def recent_leave_card(r: "LeaveRequest") -> str:
        reason = f"<div class='leave-card-reason'>{escape(r.reason)}</div>" if r.reason else ""
        return (
            "<article class='leave-card'>"
            "<div class='leave-card-main'>"
            f"<div class='leave-card-title'>{escape(r.student_display or r.student_name)} · {escape(r.leave_type or '')}</div>"
            f"<div class='leave-card-meta'>{escape(str(r.start_date))} ~ {escape(str(r.end_date))} · {escape(_leave_status_text(r.status))}</div>"
            f"{reason}"
            "</div>"
            f"<div>{_leave_status_badge(r.status)}</div>"
            "</article>"
        )
    recent_html = "".join(recent_leave_card(r) for r in recent_rows) or "<div class='leave-empty'>尚無近期請假紀錄。</div>"
    submit_disabled = " disabled" if not students else ""
    warning_html = f"<div class='alert alert-warning mb-3'>{msg}</div>" if msg else ""
    is_parent_leave = role == "parent"
    hero_note = "選擇孩子、假別與日期區間，送出後老師會在審核頁處理。" if is_parent_leave else "替學生建立請假紀錄，建立後可在請假審核頁查看與處理。"
    primary_href = "/leave/mine" if is_parent_leave else "/leave/manage"
    primary_text = "我的請假" if is_parent_leave else "請假審核"
    secondary_href = "/parent" if is_parent_leave else url_for(role_endpoint(role))
    secondary_text = "返回家長首頁" if is_parent_leave else "返回工作區"
    submit_text = "送出申請" if is_parent_leave else "新增請假紀錄"
    apply_title = "新增請假" if is_parent_leave else "新增學生請假"
    recent_title = "近期紀錄" if is_parent_leave else "近期請假紀錄"
    flow_second_title = "2. 等待審核" if is_parent_leave else "2. 進入審核"
    flow_second_text = "導師會核准或駁回，並留下備註。" if is_parent_leave else "建立後可直接在請假審核頁處理狀態。"
    flow_third_text = "可在我的請假查看目前狀態。" if is_parent_leave else "可在請假審核查看目前狀態。"
    content = f"""
    {_leave_ui_style()}
    <div class='leave-page'>
      <section class='leave-hero'>
        <div>
          <div class='leave-kicker'>Leave Request</div>
          <h3>請假申請</h3>
          <div class='leave-muted mt-1'>{hero_note}</div>
        </div>
        <div class='leave-hero-actions'>
          <a class='btn btn-outline-secondary' href='{primary_href}'>{primary_text}</a>
          <a class='btn btn-outline-secondary' href='{secondary_href}'>{secondary_text}</a>
        </div>
      </section>
      <section class='leave-flow'>
        <div><strong>1. 填寫資料</strong><span>確認孩子、假別、日期與原因。</span></div>
        <div><strong>{flow_second_title}</strong><span>{flow_second_text}</span></div>
        <div><strong>3. 查詢結果</strong><span>{flow_third_text}</span></div>
      </section>
      <section class='card border-0 shadow-sm leave-panel'>
        <div class='card-body'>
          <div class='leave-panel-head'>
            <div><div class='leave-kicker'>Apply</div><h4>{apply_title}</h4></div>
          </div>
          {warning_html}
          <form method='post' class='row g-3'>
            <div class='col-lg-6 col-md-8 col-sm-12'>
              <label class='form-label'>學生</label>
              <select name='student' class='form-select' required>{stu_opts_html}</select>
            </div>
            <div class='col-lg-6 col-md-4 col-sm-12'>
              <label class='form-label'>假別</label>
              <select name='leave_type' class='form-select' required>{leave_opts_html}</select>
            </div>
            <div class='col-md-6'>
              <label class='form-label'>開始日</label>
              <input type='date' name='start_date' class='form-control' value='{today_str}' required>
            </div>
            <div class='col-md-6'>
              <label class='form-label'>結束日</label>
              <input type='date' name='end_date' class='form-control' value='{today_str}' required>
            </div>
            <div class='col-12'>
              <label class='form-label'>事由或說明</label>
              <textarea name='reason' class='form-control' rows='4' placeholder='例如：身體不適、家庭活動、就醫回診等。'></textarea>
            </div>
            <div class='col-12 leave-form-actions'>
              <button class='btn btn-primary'{submit_disabled}>{submit_text}</button>
              <a class='btn btn-outline-secondary' href='{primary_href}'>{primary_text}</a>
            </div>
          </form>
        </div>
      </section>
      <section class='card border-0 shadow-sm leave-panel'>
        <div class='card-body'>
          <div class='leave-panel-head'>
            <div><div class='leave-kicker'>Recent</div><h4>{recent_title}</h4></div>
          </div>
          <div class='leave-card-list'>{recent_html}</div>
        </div>
      </section>
    </div>
    """
    return page("請假申請", content, msg=msg)

@app.route("/leave/mine", methods=["GET", "POST"])
@login_required
def leave_mine():
    """
    我的請假：
      - 學生：只看自己
      - 家長：看名下所有孩子的請假（無論由誰送出），且可在「待審 / 已駁回」時撤回
      - 其他角色：導回請假管理/檢視頁或顯示空
    """
    if current_user.role in ("teacher", "leader", "admin"):
        return redirect(url_for("leave_manage"))
    
    if request.method == "POST" and current_user.role == "parent":
        act = (request.form.get("action") or "").strip()
        rid = request.form.get("rid")
        if act == "withdraw" and rid:
            lr = LeaveRequest.query.get(rid)
            
            child_usernames = set(parent_child_student_usernames())
            if not lr or lr.student_name not in child_usernames:
                return toast_redirect("leave_mine", "無權撤回這筆請假。", "warning")
            if lr.status not in (None, 0):
                return toast_redirect("leave_mine", "僅能撤回「待審」或「已駁回」的請假。", "warning")
            db.session.delete(lr)
            db.session.commit()
            return toast_redirect("leave_mine", "已撤回請假。", "success")

    
    rows = []
    if current_user.role == "student":
        rows = (LeaveRequest.query
                .filter(LeaveRequest.student_name == current_user.username)
                .order_by(LeaveRequest.created_at.desc())
                .all())
    elif current_user.role == "parent":
        
        child_usernames = parent_child_student_usernames()
        if child_usernames:
            rows = (LeaveRequest.query
                    .filter(LeaveRequest.student_name.in_(child_usernames))
                    .order_by(LeaveRequest.created_at.desc())
                    .all())
        else:
            rows = []
    else:
        
        rows = []

    cards = []
    for r in rows:
        scope = f"{r.grade or ''}年{r.class_no or ''}班"
        period = f"{r.start_date} ~ {r.end_date}"
        reason_html = f"<div class='leave-card-reason'>事由：{escape(r.reason)}</div>" if r.reason else ""
        review_html = ""
        if r.status is not None and (r.review_note or r.reviewed_by):
            review_html = (
                "<div class='leave-review-note'>"
                f"審核：{escape(r.reviewed_by or '—')}"
                f"{('<br>備註：' + escape(r.review_note)) if r.review_note else ''}"
                "</div>"
            )
        
        withdraw_btn = ""
        if current_user.role == "parent" and r.status in (None, 0):
            withdraw_btn = (
                "<form method='post'>"
                f"<input type='hidden' name='rid' value='{r.id}'>"
                "<button name='action' value='withdraw' class='btn btn-sm btn-outline-secondary' "
                "onclick=\"return confirm('確定要撤回這筆請假嗎？')\">撤回</button>"
                "</form>"
            )
        cards.append(
            "<article class='leave-card'>"
            "<div class='leave-card-main'>"
            f"<div class='leave-card-title'>{escape(r.student_display or r.student_name)}（{escape(scope)}）</div>"
            f"<div class='leave-card-meta'>{escape(period)} · {escape(r.leave_type or '')}</div>"
            f"{reason_html}{review_html}"
            "</div>"
            "<div class='leave-card-actions'>"
            f"{_leave_status_badge(r.status)}"
            f"{withdraw_btn}"
            "</div>"
            "</article>"
        )

    pending_count = sum(1 for r in rows if r.status is None)
    approved_count = sum(1 for r in rows if r.status == 1)
    rejected_count = sum(1 for r in rows if r.status == 0)
    cards_html = "".join(cards) if cards else "<div class='leave-empty'>尚無請假紀錄。</div>"
    action_btn = "<a class='btn btn-primary' href='/leave'>新增請假</a>" if current_user.role == "parent" else ""
    content = f"""
    {_leave_ui_style()}
    <div class='leave-page'>
      <section class='leave-hero'>
        <div>
          <div class='leave-kicker'>My Requests</div>
          <h3>我的請假</h3>
          <div class='leave-muted mt-1'>查看請假申請狀態；待審或已駁回的申請可撤回後重新送出。</div>
        </div>
        <div class='leave-hero-actions'>
          {action_btn}
          <a class='btn btn-outline-secondary' href='{url_for(role_endpoint(current_user.role))}'>返回工作區</a>
        </div>
      </section>
      <section class='leave-summary'>
        <div><span>全部</span><strong>{len(rows)}</strong></div>
        <div><span>待審核</span><strong>{pending_count}</strong></div>
        <div><span>已核准</span><strong>{approved_count}</strong></div>
        <div><span>已駁回</span><strong>{rejected_count}</strong></div>
      </section>
      <section class='card border-0 shadow-sm leave-panel'>
        <div class='card-body'>
          <div class='leave-panel-head'>
            <div><div class='leave-kicker'>Records</div><h4>申請紀錄</h4></div>
          </div>
          <div class='leave-card-list'>{cards_html}</div>
        </div>
      </section>
    </div>
    """
    return page("我的請假", content)

@app.route("/leave/manage", methods=["GET","POST"])
@login_required
def leave_manage():
    """
    請假檢視＋管理（合併版）
    權限可見：
      - leader/admin：全校
      - 班導：只看自己班
      - 其他角色：無權；導回我的請假
    功能：
      - 依日期查詢（區間重疊者即顯示）
      - 待審清單直接核准/駁回（駁回需填理由）
      - 老師/組長/管理員可刪除（班導僅限自己班）
    """
    
    def _is_staff():
        return current_user.role in ("leader", "admin", "teacher")

    def _is_homeroom_teacher():
        return (
            current_user.role == "teacher"
            and str(getattr(current_user, "is_homeroom", 0)) == "1"
            and current_user.grade and current_user.class_no
        )

    def _can_delete_leave(lr: "LeaveRequest"):
        if current_user.role in ("leader", "admin"):
            return True
        if _is_homeroom_teacher() and lr.grade == current_user.grade and lr.class_no == current_user.class_no:
            return True
        return False

    if not _is_staff():
        return redirect(url_for("leave_mine"))

    
    if request.method == "POST":
        act  = (request.form.get("action") or "").strip()
        rid  = request.form.get("rid")
        note = (request.form.get("note") or "").strip()
        lr   = LeaveRequest.query.get(rid) if rid else None
        if not lr:
            return toast_redirect("leave_manage", "資料不存在。", "warning")

        can_review = (current_user.role in ("leader", "admin")) or (
            _is_homeroom_teacher() and lr.grade == current_user.grade and lr.class_no == current_user.class_no
        )
        if not can_review:
            return toast_redirect("leave_manage", "無權操作。", "warning")

        if act == "approve":
            lr.status = 1
            lr.review_note = note or None
            lr.reviewed_by = current_user.username
            lr.reviewed_at = datetime.utcnow()
            db.session.commit()
            return toast_redirect("leave_manage", "已核准。", "success")

        elif act == "reject":
            if not note:
                return toast_redirect("leave_manage", "請輸入駁回理由。", "warning")
            lr.status = 0
            lr.review_note = note
            lr.reviewed_by = current_user.username
            lr.reviewed_at = datetime.utcnow()
            db.session.commit()
            return toast_redirect("leave_manage", "已駁回。", "success")

        elif act == "delete":
            if not _can_delete_leave(lr):
                return toast_redirect("leave_manage", "無權刪除。", "warning")
            db.session.delete(lr)
            db.session.commit()
            return toast_redirect("leave_manage", "已刪除。", "success")

    
    qdate_str = (request.args.get("date") or today().isoformat()).strip()
    try:
        qdate = datetime.strptime(qdate_str, "%Y-%m-%d").date()
    except Exception:
        qdate = today()

    
    prev_date = (qdate - timedelta(days=1)).isoformat()
    next_date = (qdate + timedelta(days=1)).isoformat()
    today_date = today().isoformat()

    
    q = LeaveRequest.query.filter(
        LeaveRequest.start_date <= qdate,
        LeaveRequest.end_date >= qdate
    )
    if current_user.role in ("leader", "admin"):
        pass
    elif _is_homeroom_teacher():
        q = q.filter(
            LeaveRequest.grade == current_user.grade,
            LeaveRequest.class_no == current_user.class_no
        )
    else:
        q = q.filter(text("1=0"))

    rows = q.order_by(LeaveRequest.grade, LeaveRequest.class_no, LeaveRequest.student_display).all()

    
    grouped = {}
    for r in rows:
        key = f"{r.grade}年{r.class_no}班"
        grouped.setdefault(key, []).append(r)

    def _row_li(r: "LeaveRequest"):
        scope = f"{r.grade or ''}年{r.class_no or ''}班"
        period = f"{r.start_date} ~ {r.end_date}"
        reason_html = f"<div class='leave-card-reason'>事由：{escape(r.reason)}</div>" if r.reason else ""
        reviewed_html = ""
        if r.status is not None:
            note_html = f"<br>備註：{escape(r.review_note)}" if r.review_note else ""
            reviewed_html = (
                "<div class='leave-review-note'>"
                f"審核：{escape(r.reviewed_by or '—')} @ {escape(str(r.reviewed_at or '—'))}"
                f"{note_html}"
                "</div>"
            )

        ops = []
        if r.status is None:
            ops.append("<button name='action' value='approve' class='btn btn-sm btn-success'>核准</button>")
            ops.append("<button name='action' value='reject' class='btn btn-sm btn-outline-danger reject-btn'>駁回</button>")
        if _can_delete_leave(r):
            ops.append(
                "<button name='action' value='delete' class='btn btn-sm btn-outline-danger' "
                "onclick=\"return confirm('確定要刪除此請假單？')\">刪除</button>"
            )
        ops_html = " ".join(ops)

        form_html = (
            "<form method='post' class='leave-review-form'>"
              f"<input type='hidden' name='rid' value='{r.id}'>"
              "<textarea name='note' class='form-control note' rows='2' placeholder='審核備註，駁回時必填。'></textarea>"
              f"<div class='leave-card-actions'>{ops_html}</div>"
            "</form>"
        ) if ops else ""

        return (
            "<article class='leave-card'>"
            "<div class='leave-card-main'>"
            f"<div class='leave-card-title'>{escape(r.student_display or r.student_name)}（{escape(scope)}）</div>"
            f"<div class='leave-card-meta'>{escape(period)} · {escape(r.leave_type or '')} · 申請人 {escape(r.applicant_name or '')}</div>"
            f"{reason_html}{reviewed_html}"
            "</div>"
            "<div class='leave-card-actions'>"
            f"{_leave_status_badge(r.status)}"
            f"<a class='leave-detail-link' href='{url_for('leave_review_detail', leave_id=r.id)}'>詳細審核</a>"
            f"{form_html}"
            "</div>"
            "</article>"
        )

    
    nav = (
        "<form method='get' class='leave-date-nav'>"
          "<div class='leave-date-form'>"
          f"<input type='date' name='date' value='{qdate.isoformat()}' class='form-control'>"
          "<button class='btn btn-outline-primary'>查詢</button>"
          "</div>"
          f"<a class='btn btn-outline-secondary' href='?date={prev_date}'>前一天</a>"
          f"<a class='btn btn-outline-secondary' href='?date={today_date}'>今天</a>"
          f"<a class='btn btn-outline-secondary' href='?date={next_date}'>下一天</a>"
        "</form>"
    )

    blocks = []
    for key, lst in grouped.items():
        lis = "".join(_row_li(r) for r in lst)
        blocks.append(
            "<section class='card border-0 shadow-sm leave-panel'>"
            "<div class='card-body'>"
            "<div class='leave-panel-head'>"
            f"<div><div class='leave-kicker'>Class</div><h5>{escape(key)}</h5></div>"
            f"<span class='badge bg-light text-muted border'>{len(lst)} 筆</span>"
            "</div>"
            f"<div class='leave-card-list'>{lis}</div>"
            "</div></section>"
        )

    
    script = (
        "<script>"
        "document.addEventListener('click',function(e){"
          "if(e.target && e.target.classList.contains('reject-btn')){"
            "const form=e.target.closest('.leave-review-form');"
            "const note=form?.querySelector('.note');"
            "if(note && !note.value.trim()){"
              "e.preventDefault();"
              "alert('請輸入駁回理由');"
            "}"
          "}"
        "});"
        "</script>"
    )

    pending_count = sum(1 for r in rows if r.status is None)
    approved_count = sum(1 for r in rows if r.status == 1)
    rejected_count = sum(1 for r in rows if r.status == 0)
    list_html = "".join(blocks) if blocks else "<section class='card border-0 shadow-sm leave-panel'><div class='card-body'><div class='leave-empty'>這一天沒有請假紀錄。</div></div></section>"
    content = f"""
    {_leave_ui_style()}
    <div class='leave-page'>
      <section class='leave-hero'>
        <div>
          <div class='leave-kicker'>Leave Review</div>
          <h3>請假審核</h3>
          <div class='leave-muted mt-1'>查看 {qdate.isoformat()} 有效的請假申請，可直接核准、駁回或刪除錯誤資料。</div>
        </div>
        <div class='leave-hero-actions'>
          <a class='btn btn-primary' href='{url_for("leave_apply")}'>新增請假</a>
          {nav}
        </div>
      </section>
      <section class='leave-summary'>
        <div><span>當日有效</span><strong>{len(rows)}</strong></div>
        <div><span>待審核</span><strong>{pending_count}</strong></div>
        <div><span>已核准</span><strong>{approved_count}</strong></div>
        <div><span>已駁回</span><strong>{rejected_count}</strong></div>
      </section>
      {list_html}
      {script}
    </div>
    """
    return page("請假檢視 / 管理", content)

@app.route("/leave/review/<int:leave_id>", methods=["GET", "POST"])
@roles_required("teacher", "leader", "admin")
def leave_review_detail(leave_id: int):
    lr = LeaveRequest.query.get_or_404(leave_id)
    if not _can_review_leave_request(lr):
        return toast_redirect("leave_manage", "沒有權限審核這筆請假。", "warning")

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        note = (request.form.get("note") or "").strip()
        back_date = lr.start_date.isoformat() if lr.start_date else today().isoformat()
        ok, message = _apply_leave_review_action(lr, action, note)
        if ok:
            db.session.commit()
            return toast_redirect("leave_manage", message, "success", date=back_date)
        db.session.rollback()
        return toast_redirect("leave_review_detail", message, "warning", leave_id=leave_id)

    period = f"{lr.start_date} ~ {lr.end_date}"
    scope = f"{lr.unit or SCHOOL_NAME} · {lr.grade or ''}年{lr.class_no or ''}班"
    applicant = f"{display_name_of(lr.applicant_name)}（{lr.applicant_role or '申請人'}）"
    created_at = lr.created_at.strftime("%Y-%m-%d %H:%M") if lr.created_at else "—"
    reviewed_at = lr.reviewed_at.strftime("%Y-%m-%d %H:%M") if lr.reviewed_at else "尚未審核"
    reason_html = _record_text(lr.reason) if lr.reason else "家長未填寫補充說明。"
    review_note_html = _record_text(lr.review_note) if lr.review_note else "尚無審核備註。"
    back_date = lr.start_date.isoformat() if lr.start_date else today().isoformat()

    content = f"""
    {_leave_ui_style()}
    <div class='leave-page'>
      <section class='leave-hero'>
        <div>
          <div class='leave-kicker'>Single Review</div>
          <h3>{escape(lr.student_display or lr.student_name)} 的請假申請</h3>
          <div class='leave-muted mt-1'>{escape(scope)} · {escape(period)}</div>
        </div>
        <div class='leave-hero-actions'>
          {_leave_status_badge(lr.status)}
          <a class='btn btn-outline-secondary' href='{url_for("leave_manage", date=back_date)}'>回請假審核</a>
        </div>
      </section>

      <section class='leave-summary'>
        <div><span>假別</span><strong>{escape(lr.leave_type or "—")}</strong></div>
        <div><span>申請人</span><strong>{escape(applicant)}</strong></div>
        <div><span>送出時間</span><strong>{escape(created_at)}</strong></div>
        <div><span>審核時間</span><strong>{escape(reviewed_at)}</strong></div>
      </section>

      <section class='leave-flow'>
        <div><strong>1. 檢查區間</strong><span>確認日期與假別是否符合實際狀況。</span></div>
        <div><strong>2. 閱讀原因</strong><span>必要時在備註補充老師處理說明。</span></div>
        <div><strong>3. 送出結果</strong><span>核准或駁回後，家長可在我的請假看到狀態。</span></div>
      </section>

      <div class='leave-detail-grid'>
        <section class='leave-detail-panel'>
          <div class='leave-detail-head'>
            <div><div class='leave-kicker'>Request Detail</div><h4>請假內容</h4></div>
            {_leave_status_badge(lr.status)}
          </div>
          <div class='leave-detail-meta'>
            <div><span>學生</span><strong>{escape(lr.student_display or lr.student_name)}</strong></div>
            <div><span>班級</span><strong>{escape(f"{lr.grade or ''}年{lr.class_no or ''}班")}</strong></div>
            <div><span>開始日</span><strong>{escape(str(lr.start_date))}</strong></div>
            <div><span>結束日</span><strong>{escape(str(lr.end_date))}</strong></div>
          </div>
          <div class='leave-detail-box'>{reason_html}</div>
          <div class='leave-review-note mt-3'>
            <strong>目前審核紀錄</strong><br>
            審核者：{escape(display_name_of(lr.reviewed_by) if lr.reviewed_by else "尚未審核")}<br>
            備註：{review_note_html}
          </div>
        </section>

        <section class='leave-detail-panel leave-detail-panel--sticky'>
          <div class='leave-detail-head'>
            <div><div class='leave-kicker'>Decision</div><h4>審核決定</h4></div>
          </div>
          <form method='post' class='leave-detail-form'>
            <label class='form-label'>審核備註</label>
            <textarea name='note' class='form-control' rows='5' placeholder='核准可留空；駁回時請寫清楚原因，方便家長理解。'></textarea>
            <div class='leave-form-actions mt-3'>
              <button class='btn btn-primary' name='action' value='approve'>核准請假</button>
              <button class='btn btn-outline-danger' name='action' value='reject'>駁回申請</button>
              <button class='btn btn-outline-danger' name='action' value='delete' onclick="return confirm('確定要刪除此請假單？此動作無法復原。');">刪除</button>
            </div>
          </form>
        </section>
      </div>
    </div>
    """
    return page("單筆請假審核", content)


@app.route("/template/students.csv")
@roles_required("leader")
def template_students_csv():
    sample = [
        ["學號","姓名","年級","班級"],
        ["10001","王小明","3","2"],
        ["10002","林小華","3","2"],
    ]
    sio = io.StringIO()
    writer = csv.writer(sio, lineterminator="\r\n")
    writer.writerows(sample)
    csv_text = sio.getvalue()
    data = ("\ufeff" + csv_text).encode("utf-8")
    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=student_template.csv"}
    )

def parse_students_csv(file_storage):
    raw = file_storage.read()
    encodings_to_try = ("utf-8-sig", "utf-8", "cp950", "big5", "gbk", "gb18030")
    text = None
    for enc in encodings_to_try:
        try:
            text = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    if not rows: return [], "名冊檔案內容為空。"
    header_map = {
        "學號":"username","姓名":"display_name","年級":"grade","班級":"class_no",
        "username":"username","display_name":"display_name","grade":"grade","class_no":"class_no"
    }
    header = rows[0]
    has_header = any(h in header_map for h in header)
    data_rows = rows[1:] if has_header else rows
    parsed = []
    for idx, r in enumerate(data_rows, start=2 if has_header else 1):
        if len(r) < 4:
            return [], f"第 {idx} 行欄位不足（需 4 欄：學號,姓名,年級,班級）。"
        sid, name, gg, cc = r[0], r[1], r[2], r[3]
        parsed.append((sid, name, gg, cc, idx))
    return parsed, None


def teacher_can_review(task: Task, teacher: User) -> bool:
    """同校，且（本班任務）或（全校任務）"""
    if task.unit != teacher.unit:
        return False
    if task.is_school_wide == 1:
        return True
    return (task.grade == teacher.grade and task.class_no == teacher.class_no)

def can_review(task: Task, reviewer: User) -> bool:
    """老師：teacher_can_review；組長：同校任務皆可審；其他不行"""
    if reviewer.role == "teacher":
        return teacher_can_review(task, reviewer)
    if reviewer.role == "leader":
        return task.unit == reviewer.unit
    return False

@app.route("/leader", methods=["GET", "POST"])
@roles_required("leader")
def leader():
    unit = current_user.unit
    msg = ""
    F = lambda k, d="": (request.form.get(k) or d).strip()

    
    sem_btns = (
        "<div class='leader-action-grid'>"
        f"<a class='leader-action' href='{url_for('semester_manage')}'><strong>學期週次</strong></a>"
        f"<a class='leader-action' href='{url_for('timetable_images')}'><strong>課表圖片</strong></a>"
        f"<a class='leader-action' href='{url_for('leave_manage')}'><strong>請假審核</strong></a>"
        f"<a class='leader-action' href='{url_for('medication_records')}'><strong>用藥紀錄</strong></a>"
        f"<a class='leader-action' href='{url_for('reading_review_queue')}'><strong>閱讀審核</strong></a>"
        f"<a class='leader-action' href='{url_for('teacher_assignments_page')}'><strong>授課科目</strong></a>"
        "</div>"
    )

    
    
    
    if request.method == "POST" and F("form") == "task":
        title = F("title")
        desc  = F("description")
        cat   = F("category") or "其他"
        pts   = F("points") or "0"
        scope = F("scope", "school")

        points = int(pts) if pts.isdigit() else 0
        end    = parse_date(F("end_date"))
        gsel, csel = F("grade"), F("class_no")

        if title and desc:
            t = Task(
                title=title,
                description=desc,
                created_by=current_user.username,
                category=cat,
                points=max(points, 0),
                start_date=today(),
                end_date=end,
                unit=unit,
                task_type="homework",
            )
            
            t.is_view_only = 1 if request.form.get("is_view_only") else 0

            if scope == "school":
                t.is_school_wide = 1
            elif scope == "grade" and gsel in GRADE_OPTIONS:
                t.grade = gsel
            elif scope == "class" and (gsel in GRADE_OPTIONS and csel in CLASS_OPTIONS):
                t.grade, t.class_no = gsel, csel
            else:
                msg = "範圍設定不正確。"

            if not msg:
                db.session.add(t)
                db.session.commit()
                return toast_redirect("leader", "已發布本校作業。", "success")

    
    
    
    if request.method == "POST" and F("form") == "mission":
        title = F("title")
        desc  = F("description")
        mcat  = F("mission_category") or "其他"
        pts   = F("points") or "0"
        scope = F("scope", "school")

        points = int(pts) if pts.isdigit() else 10
        end    = parse_date(F("end_date"))
        gsel, csel = F("grade"), F("class_no")

        if title and desc:
            t = Task(
                title=title,
                description=desc,
                created_by=current_user.username,
                mission_category=mcat,
                points=max(points, 10),
                start_date=today(),
                end_date=end,
                unit=unit,
                task_type="mission",
            )
            t.is_view_only = 1 if request.form.get("is_view_only") else 0

            if scope == "school":
                t.is_school_wide = 1
            elif scope == "grade" and gsel in GRADE_OPTIONS:
                t.grade = gsel
            elif scope == "class" and (gsel in GRADE_OPTIONS and csel in CLASS_OPTIONS):
                t.grade, t.class_no = gsel, csel
            else:
                msg = "範圍設定不正確。"

            if not msg:
                db.session.add(t)
                db.session.commit()
                return toast_redirect("leader", "已發布閱讀／永續任務。", "success")

    
    
    
    if request.method == "POST" and F("form") == "csv":
        f = request.files.get("csv")
        if not f or not f.filename.lower().endswith(".csv"):
            msg = "請選擇學生名冊檔。"
        else:
            parsed, err = parse_students_csv(f)
            if err:
                msg = f"匯入失敗：{err}"
            else:
                added = 0
                skipped_exist = 0
                invalid = 0
                duplicated = 0
                seen = set()
                for sid, name, gg, cc, rowno in parsed:
                    if sid in seen:
                        duplicated += 1
                        continue
                    seen.add(sid)
                    if (not sid.isdigit()) or (gg not in GRADE_OPTIONS) or (cc not in CLASS_OPTIONS) or not name:
                        invalid += 1
                        continue
                    if User.query.filter_by(username=sid).first():
                        skipped_exist += 1
                        continue
                    db.session.add(User(
                        username=sid,
                        display_name=name,
                        password="0000",
                        role="student",
                        unit=unit,
                        grade=gg,
                        class_no=cc
                    ))
                    added += 1
                db.session.commit()
                return toast_redirect(
                    "leader",
                    f"名冊匯入完成：新增 {added}；已存在 {skipped_exist}；格式不完整 {invalid}；重複 {duplicated}。",
                    "success",
                )

    
    
    
    tasks_all = (
        Task.query
        .filter(Task.unit == unit, Task.task_type == "homework")
        .order_by(Task.id.desc())
        .all()
    )
    missions_all = (
        Task.query
        .filter(Task.unit == unit, Task.task_type == "mission")
        .order_by(Task.id.desc())
        .all()
    )

    def trow(t: "Task"):
        tools = (
            f"<a class='btn btn-sm btn-warning me-2' href='/edit_task/{t.id}'>改</a>"
            f"<a class='btn btn-sm btn-danger' href='/delete_task/{t.id}' "
            f"onclick='return confirm(\"刪除此作業？\");'>刪</a>"
        )
        vo_badge = " <span class='badge bg-secondary ms-1'>僅供閱讀</span>" if getattr(t, "is_view_only", 0) else ""
        return (
            "<tr>"
            f"<td class='text-nowrap'>{t.id}</td>"
            f"<td>{t.title}{vo_badge}</td>"
            f"<td><span class='badge bg-dark'>{scope_txt(t)}</span></td>"
            f"<td><span class='badge bg-secondary'>{t.category or '其他'}</span> "
            f"<span class='badge bg-info'>+{t.points or 0} 分</span></td>"
            f"<td class='text-nowrap'>{t.end_date or '—'}</td>"
            f"<td class='text-end'>{tools}</td>"
            "</tr>"
        )

    body_tasks = "".join(map(trow, tasks_all)) or "<tr><td colspan='6' class='text-center'>本校尚無作業</td></tr>"

    def mrow(t: "Task"):
        tools = (
            f"<a class='btn btn-sm btn-warning me-2' href='/edit_task/{t.id}'>改</a>"
            f"<a class='btn btn-sm btn-danger' href='/delete_task/{t.id}' "
            f"onclick='return confirm(\"刪除此閱讀任務？\");'>刪</a>"
        )
        vo_badge = " <span class='badge bg-secondary ms-1'>僅供閱讀</span>" if getattr(t, "is_view_only", 0) else ""
        return (
            "<tr>"
            f"<td class='text-nowrap'>{t.id}</td>"
            f"<td>{t.title}{vo_badge}</td>"
            f"<td><span class='badge bg-dark'>{scope_txt(t)}</span></td>"
            f"<td><span class='badge bg-success'>{t.mission_category or '其他'}</span> "
            f"<span class='badge bg-info'>+{t.points or 0} 分</span></td>"
            f"<td class='text-nowrap'>{t.end_date or '—'}</td>"
            f"<td class='text-end'>{tools}</td>"
            "</tr>"
        )

    body_missions = "".join(map(mrow, missions_all)) or "<tr><td colspan='6' class='text-center'>本校尚無閱讀任務</td></tr>"

    
    
    
    students = (
        User.query
        .filter_by(role="student", unit=unit)
        .order_by(User.grade, User.class_no, User.username)
        .all()
    )

    srows = "".join([
        f"<tr><td>{s.username}</td><td>{s.display_name or ''}</td>"
        f"<td>{s.grade}年{s.class_no}班</td>"
        f"<td class='text-end'><a class='btn btn-sm btn-outline-danger' "
        f"href='/leader_del_student/{s.username}' "
        f"onclick='return confirm(\"刪除學生 {s.username}？\");'>刪除</a></td></tr>"
        for s in students
    ]) or "<tr><td colspan='4' class='text-center'>尚無學生</td></tr>"

    
    
    
    diary_prompts = (
        DiaryPrompt.query
        .filter_by(unit=unit)
        .order_by(DiaryPrompt.date.desc(), DiaryPrompt.created_at.desc())
        .limit(100)
        .all()
    )

    def drow(p: "DiaryPrompt"):
        return (
            "<tr>"
            f"<td class='text-nowrap'>{p.date}</td>"
            f"<td>{p.title}</td>"
            f"<td class='text-nowrap'>{p.grade}年{p.class_no}班</td>"
            f"<td class='text-nowrap'>{display_name_of(p.created_by)}</td>"
            f"<td class='text-end text-nowrap'>"
            f"<a class='btn btn-sm btn-outline-danger' href='/leader/diary/delete/{p.id}' "
            "onclick='return confirm(\"刪除此題目與其日記提交？\");'>刪除</a></td>"
            "</tr>"
        )

    diary_body = "".join(map(drow, diary_prompts)) or "<tr><td colspan='5' class='text-center'>尚無日記題目</td></tr>"

    
    
    
    teacher_count = User.query.filter_by(role="teacher", unit=unit).count()
    parent_count = User.query.filter_by(role="parent", unit=unit).count()
    pending_leave_count = (
        LeaveRequest.query
        .filter(LeaveRequest.unit == unit, LeaveRequest.status.is_(None))
        .count()
    )
    pending_reading_count = (
        CompletedTask.query
        .join(Task, CompletedTask.task_id == Task.id)
        .filter(Task.unit == unit, Task.task_type == "mission", CompletedTask.approved.is_(None))
        .count()
    )
    pending_med_count = (
        MedicationRecord.query
        .filter_by(unit=unit, date=today(), status="pending")
        .count()
    )

    head = f"""
    <div class='leader-page'>
      <section class='leader-hero'>
        <div>
          <div class='leader-eyebrow'>學校管理</div>
          <h2>組長管理中心</h2>
          <div class='leader-school'>{escape(unit or '')}</div>
        </div>
        <a class='btn btn-outline-secondary btn-sm' href='/profile'>個人檔案</a>
      </section>
      <section class='leader-metrics'>
        <div><strong>{len(students)}</strong><span>學生帳號</span></div>
        <div><strong>{teacher_count}</strong><span>教師帳號</span></div>
        <div><strong>{parent_count}</strong><span>家長帳號</span></div>
        <div><strong>{len(tasks_all)}</strong><span>校內作業</span></div>
        <div><strong>{len(missions_all)}</strong><span>閱讀任務</span></div>
        <div><strong>{pending_reading_count}</strong><span>待審閱讀</span></div>
        <div><strong>{pending_leave_count}</strong><span>待審請假</span></div>
        <div><strong>{pending_med_count}</strong><span>今日待處理用藥</span></div>
      </section>
      {sem_btns}
    """

    grade_opts = opt(GRADE_OPTIONS)
    class_opts = opt(CLASS_OPTIONS)
    mission_opts = "".join(f"<option value='{c}'>{c}</option>" for c in MISSION_CATEGORY_OPTIONS)

    def leader_scope_fields(scope_id: str, desc_placeholder: str) -> str:
        return (
            f"<div><label class='form-label'>範圍</label><select name='scope' id='{scope_id}' class='form-select form-select-lg'>"
            "<option value='school'>全校</option><option value='grade'>年級</option><option value='class'>班級</option>"
            "</select></div>"
            f"<div class='scope-grade' style='display:none;'><label class='form-label'>年級</label>"
            f"<select name='grade' class='form-select form-select-lg'><option value=''>請選擇年級</option>{grade_opts}</select></div>"
            f"<div class='scope-class' style='display:none;'><label class='form-label'>班級</label>"
            f"<select name='class_no' class='form-select form-select-lg'><option value=''>請選擇班級</option>{class_opts}</select></div>"
            "<div><label class='form-label'>截止日</label>"
            f"<input type='date' name='end_date' class='form-control form-control-lg' value='{today()}'></div>"
            "<div><label class='form-label'>描述</label>"
            f"<textarea name='description' class='form-control' rows='4' placeholder='{desc_placeholder}' required></textarea></div>"
            f"<script>const sc_{scope_id}=document.getElementById('{scope_id}');"
            f"function vis_{scope_id}(){{const v=sc_{scope_id}.value;const box=sc_{scope_id}.closest('form')||document;"
            f"const g=box.querySelector('.scope-grade');const c=box.querySelector('.scope-class');"
            f"if(g)g.style.display=(v==='grade'||v==='class')?'block':'none';"
            f"if(c)c.style.display=(v==='class')?'block':'none';}}"
            f"sc_{scope_id}.addEventListener('change',vis_{scope_id});"
            f"document.addEventListener('DOMContentLoaded',vis_{scope_id});vis_{scope_id}();</script>"
        )

    
    form_task = (
        "<section class='leader-panel leader-panel--publish'>"
        "<div class='leader-panel__head'><div><div class='leader-section-label'>校內作業</div><h3>發布本校作業</h3></div></div>"
        "<form method='post' class='leader-publish-form'><input type='hidden' name='form' value='task'>"
        "<div class='leader-form-stack'>"
        "<div><label class='form-label'>標題</label>"
        "<input name='title' class='form-control form-control-lg' placeholder='請輸入作業標題' required></div>"
        "<div><label class='form-label'>分類</label>"
        "<select name='category' class='form-select form-select-lg'>"
        + "".join(f"<option value='{c}'>{c}</option>" for c in CATEGORY_OPTIONS) +
        "</select></div>"
        "<div><label class='form-label'>積分</label>"
        "<input type='number' name='points' class='form-control form-control-lg' value='0' min='0' step='1'></div>"
        + leader_scope_fields("ld_scope", "請輸入作業內容、提醒事項或學生需要完成的重點。")
        + "</div>"
        "<div class='leader-form-footer'>"
        "<div class='form-check'>"
          "<input class='form-check-input' type='checkbox' name='is_view_only' id='ld_is_view_only'>"
          "<label class='form-check-label' for='ld_is_view_only'>僅供閱讀（不可繳交）</label>"
        "</div>"
        "<button class='btn btn-primary btn-lg'>發布</button></div></form>"
        "</section>"
    )

    form_mission = (
        "<section class='leader-panel leader-panel--publish leader-panel--mission'>"
        "<div class='leader-panel__head'><div><div class='leader-section-label'>永續閱讀</div><h3>發布永續閱讀任務</h3></div></div>"
        "<form method='post' class='leader-publish-form'><input type='hidden' name='form' value='mission'>"
        "<div class='leader-form-stack'>"
        "<div><label class='form-label'>任務標題</label>"
        "<input name='title' class='form-control form-control-lg' placeholder='例：SDG 主題閱讀挑戰' required></div>"
        "<div><label class='form-label'>永續分類</label>"
        f"<select name='mission_category' class='form-select form-select-lg'>{mission_opts}</select></div>"
        "<div><label class='form-label'>積分</label>"
        "<input type='number' name='points' class='form-control form-control-lg' value='10' min='10' step='1'></div>"
        + leader_scope_fields("ld_mission_scope", "請描述閱讀主題、繳交方式、心得方向或任務提醒。")
        + "</div>"
        "<div class='leader-form-footer'>"
        "<div class='form-check'>"
          "<input class='form-check-input' type='checkbox' name='is_view_only' id='ld_mission_view_only'>"
          "<label class='form-check-label' for='ld_mission_view_only'>僅供閱讀（不可繳交）</label>"
        "</div>"
        "<button class='btn btn-primary btn-lg'>發布閱讀任務</button></div></form>"
        "</section>"
    )
    
    table_tasks = (
        "<section class='leader-panel'>"
        "<div class='leader-panel__head'><div><div class='leader-section-label'>作業清單</div><h3>本校作業管理</h3></div></div>"
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th class='text-nowrap'>編號</th><th>標題</th><th>範圍</th>"
        "<th>分類</th><th>截止</th><th class='text-end'>操作</th></tr></thead>"
        f"<tbody>{body_tasks}</tbody></table></div>"
        "</section>"
    )

    table_missions = (
        "<section class='leader-panel'>"
        "<div class='leader-panel__head'><div><div class='leader-section-label'>閱讀清單</div><h3>永續閱讀任務管理</h3></div></div>"
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th class='text-nowrap'>編號</th><th>標題</th><th>範圍</th>"
        "<th>分類</th><th>截止</th><th class='text-end'>操作</th></tr></thead>"
        f"<tbody>{body_missions}</tbody></table></div>"
        "</section>"
    )

    
    form_students = (
        "<section class='leader-panel'>"
        "<div class='leader-panel__head'><div><div class='leader-section-label'>學生名冊</div><h3>學生管理</h3></div></div>"
        "<div class='row g-3'>"
        "<div class='col-md-5'>"
        "<div class='d-grid gap-2 mb-2'>"
        "<a class='btn btn-outline-secondary' href='/template/students.csv'>下載學生名冊範本</a>"
        "</div>"
        "<form method='post' enctype='multipart/form-data'>"
        "<input type='hidden' name='form' value='csv'>"
        "<label class='form-label'>上傳學生名冊檔</label>"
        "<input type='file' name='csv' accept='.csv' class='form-control' required>"
        "<button class='btn btn-success mt-2'>上傳並匯入</button></form>"
        "</div>"
        "<div class='col-md-7'>"
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th>學號(帳號)</th><th>姓名</th><th>班級</th><th class='text-end'>操作</th></tr></thead>"
        f"<tbody>{srows}</tbody></table></div>"
        "</div></div></section>"
    )

    
    diary_block = (
        "<section class='leader-panel'>"
        "<div class='leader-panel__head'><div><div class='leader-section-label'>日記題目</div><h3>日記管理</h3></div></div>"
        "<div class='table-responsive'><table class='table table-sm align-middle'>"
        "<thead><tr><th>日期</th><th>題目</th><th>班級</th><th>建立者</th><th class='text-end'>操作</th></tr></thead>"
        f"<tbody>{diary_body}</tbody></table></div>"
        "</section>"
    )

    return page(
        "組長後台（學校管理）",
        head
        + "<div class='leader-form-column'>"
        + form_task
        + form_mission
        + "</div>"
        + table_tasks
        + table_missions
        + form_students
        + diary_block
        + "</div>",
        msg=msg,
    )

@app.route("/leader_del_student/<student_username>", methods=["GET", "POST"])
@roles_required("leader", "admin")
def leader_del_student(student_username):
    """
    年級組長 / 管理員刪除學生帳號
    - 以 username 當作網址參數，例如：/leader_del_student/10202
    - 一併刪除 ParentChild 綁定關係
    - 其他歷史資料（閱讀認證、日記等）保留，以免影響統計
    """
    
    stu = User.query.filter_by(username=student_username, role="student").first()
    if not stu:
        return toast_redirect(
            "homework",
            f"找不到學生帳號：{student_username}",
            "warning",
        )

    
    links = ParentChild.query.filter_by(student_name=stu.username).all()
    for lk in links:
        db.session.delete(lk)

    
    name = stu.display_name or stu.username
    db.session.delete(stu)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return toast_redirect(
            "homework",
            "刪除學生時發生問題，請稍後再試或聯絡管理者。",
            "danger",
        )

    return toast_redirect(
        "homework",
        f"已刪除學生帳號：{name}",
        "success",
    )

@app.route("/admin/photos", methods=["GET", "POST"])
@roles_required("admin", "leader")
def admin_photos():
    """
    管理員／年級組長：上傳照片管理
    - GET：列出 UPLOAD_FOLDER 底下所有照片，可勾選
    - POST：刪除勾選的檔案，並清除資料庫內對這些檔名的引用
    """
    import os, json
    from datetime import datetime

    root = app.config.get("UPLOAD_FOLDER") or UPLOAD_DIR

    
    if request.method == "POST":
        to_delete = request.form.getlist("photos")
        to_delete = [p.strip() for p in to_delete if p.strip()]
        to_delete_set = set(to_delete)

        if not to_delete_set:
            return toast_redirect("admin_photos", "未選擇任何照片。", "warning")

        deleted_files = 0
        failed_files = 0

        
        if os.path.isdir(root):
            for rel in to_delete_set:
                fp = os.path.join(root, _normalize_path_text(rel))
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                        deleted_files += 1
                except Exception:
                    failed_files += 1

        
        cleared_ct = cleared_psig = cleared_diary = cleared_task = 0

        
        try:
            if "CompletedTask" in globals():
                for ct in CompletedTask.query.all():
                    changed = False

                    
                    if hasattr(ct, "proof_image") and getattr(ct, "proof_image", None) in to_delete_set:
                        ct.proof_image = None
                        changed = True

                    
                    try:
                        p = _ct_load(ct)
                    except Exception:
                        p = None

                    if isinstance(p, dict):
                        photos = p.get("photos") or []
                        new_photos = [x for x in photos if x not in to_delete_set]
                        if new_photos != photos:
                            p["photos"] = new_photos
                            changed = True

                        if p.get("parent_photo") in to_delete_set:
                            p["parent_photo"] = None
                            changed = True

                        if changed:
                            _ct_save(ct, p)

                    if changed:
                        
                        if hasattr(ct, "has_image"):
                            has_any = False
                            if getattr(ct, "proof_image", None):
                                has_any = True
                            else:
                                try:
                                    pp = _ct_load(ct)
                                    if (pp.get("photos") or []) or pp.get("parent_photo"):
                                        has_any = True
                                except Exception:
                                    pass
                            ct.has_image = 1 if has_any else 0

                        db.session.add(ct)
                        cleared_ct += 1
        except Exception:
            pass

        
        try:
            PS = globals().get("ParentSignature")
            if PS is not None:
                for ps in PS.query.all():
                    if getattr(ps, "image", None) in to_delete_set:
                        ps.image = None
                        db.session.add(ps)
                        cleared_psig += 1
        except Exception:
            pass

        
        try:
            DS = globals().get("DiarySubmission")
            if DS is not None:
                for ds in DS.query.all():
                    if hasattr(ds, "image") and getattr(ds, "image", None) in to_delete_set:
                        ds.image = None
                        db.session.add(ds)
                        cleared_diary += 1
        except Exception:
            pass

        
        try:
            if "Task" in globals():
                img_cols = (
                    "image", "attachment_image", "image_name", "image_file",
                    "cover_image", "photo", "picture", "img",
                    "mission_image", "homework_image"
                )
                for t in Task.query.all():
                    changed = False

                    
                    for col in img_cols:
                        if hasattr(t, col) and getattr(t, col, None) in to_delete_set:
                            setattr(t, col, None)
                            changed = True

                    
                    if hasattr(t, "extra") and getattr(t, "extra", None):
                        val = t.extra
                        try:
                            data = json.loads(val) if isinstance(val, str) else (val or {})
                            if isinstance(data, dict):
                                before = dict(data)
                                for k in ("image", "cover_image", "picture", "img"):
                                    if isinstance(data.get(k), str) and data.get(k) in to_delete_set:
                                        data[k] = None
                                if data != before:
                                    t.extra = json.dumps(data, ensure_ascii=False)
                                    changed = True
                        except Exception:
                            pass

                    if changed:
                        db.session.add(t)
                        cleared_task += 1
        except Exception:
            pass

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return toast_redirect(
                "admin_photos",
                "刪除照片時發生問題，部分資料可能尚未完成清理，請聯絡管理者協助確認。",
                "danger",
            )

        
        msg_parts = []
        msg_parts.append(f"已刪除照片 {deleted_files} 張")
        if failed_files:
            msg_parts.append(f"刪除失敗 {failed_files} 筆")
        if cleared_ct:
            msg_parts.append(f"閱讀認證紀錄清理 {cleared_ct} 筆")
        if cleared_psig:
            msg_parts.append(f"家長簽名紀錄清理 {cleared_psig} 筆")
        if cleared_diary:
            msg_parts.append(f"日記紀錄清理 {cleared_diary} 筆")
        if cleared_task:
            msg_parts.append(f"作業／公告圖片欄位清理 {cleared_task} 筆")

        final_msg = "；".join(msg_parts) or "已完成刪除操作。"
        return toast_redirect("admin_photos", final_msg, "success")

    
    photos = _collect_upload_photos()

    if not photos:
        content = """
        <div class='card border-0 shadow-sm'>
          <div class='card-body'>
            <h5 class='card-title mb-3'>上傳照片管理</h5>
            <div class='text-muted'>目前沒有可管理的照片。</div>
          </div>
        </div>
        """
        return page("上傳照片管理", content)

    
    cards_html = []
    for idx, p in enumerate(photos):
        rel = p["rel"]
        url = p["url"]
        mtime = p["mtime"]
        safe_id = f"ph_{idx}"
        cards_html.append(
            "<div class='col'>"
            "  <div class='card h-100'>"
            "    <div class='card-body text-center'>"
            f"      <div class='mb-2'><a href='{url}' target='_blank'>"
            f"        <img src='{url}' style='max-width:120px;max-height:120px;border-radius:8px;'></a></div>"
            f"      <div class='small text-muted' style='word-break:break-all;'>{os.path.basename(rel)}</div>"
            f"      <div class='small text-muted'>{mtime}</div>"
            "      <div class='form-check mt-1'>"
            f"        <input class='form-check-input' type='checkbox' name='photos' value='{rel}' id='{safe_id}'>"
            f"        <label class='form-check-label small' for='{safe_id}'>刪除</label>"
            "      </div>"
            "    </div>"
            "  </div>"
            "</div>"
        )

    content = f"""
    <div class='card border-0 shadow-sm'>
      <div class='card-body'>
        <h5 class='card-title mb-3'>上傳照片管理</h5>
        <form method='post'>
          <div class='row row-cols-2 row-cols-md-4 g-3'>
            {''.join(cards_html)}
          </div>
          <div class='mt-3 d-flex justify-content-between align-items-center'>
            <div class='small text-muted'>共 {len(photos)} 張照片，可勾選多張一併刪除。</div>
            <button class='btn btn-danger'
                    onclick="return confirm('確定要刪除勾選的照片嗎？此動作無法復原。');">
              刪除勾選照片
            </button>
          </div>
        </form>
      </div>
    </div>
    """
    return page("上傳照片管理", content)

@app.route("/parent_diary")
@roles_required("parent")
def parent_diary():
    """
    家長｜日記專區：
    - 依日期列出所有已綁定學童當天的日記題目與繳交狀態
    - 若孩子有公開給家長（share_to_parent=1），顯示日記內容
    - 提供『家長簽名』按鈕 → 連到 /parent_sign?scope=diary
    """
    from datetime import timedelta

    date_str = (request.args.get("date") or "").strip()
    dsel = parse_date(date_str) or local_today()
    return redirect(url_for("parent", date=dsel.isoformat()) + "#parent-diary-panel")

    links = parent_child_links()
    if not links:
        inner = (
            "<div class='parent-diary-empty'>"
            "<div class='parent-diary-empty__title'>尚未綁定學童</div>"
            "<div class='parent-diary-empty__text'>請向導師索取家長邀請碼，再到註冊頁完成綁定。</div>"
            "<a class='btn btn-primary mt-3' href='/register'>前往註冊頁</a>"
            "</div>"
        )
        return page("家長｜日記專區", inner)

    prev_d = (dsel - timedelta(days=1)).isoformat()
    next_d = (dsel + timedelta(days=1)).isoformat()
    today_iso = local_today().isoformat()

    def _safe_get(obj, *names):
        for n in names:
            if hasattr(obj, n):
                v = getattr(obj, n)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""

    def _nl_html(value: str | None) -> str:
        return _user_text_html(value)

    def _upload_img(name: str | None, cls: str, alt: str) -> str:
        if not name:
            return ""
        safe_name = _safe_upload_name(name)
        return (
            f"<a href='/uploads/{safe_name}' target='_blank' class='parent-diary-image-link' title='點擊放大圖片'>"
            f"<img src='/uploads/{safe_name}' class='{cls}' alt='{escape(alt)}'>"
            "<span class='parent-diary-image-hint'>點擊放大</span>"
            "</a>"
        )

    prompted_count = 0
    shared_count = 0
    signed_count = 0
    cards = []

    for lk in links:
        stu = User.query.filter_by(username=lk.student_name, role="student").first()
        if not stu:
            continue

        prompt = (
            DiaryPrompt.query.filter_by(
                unit=stu.unit,
                grade=stu.grade,
                class_no=stu.class_no,
                date=dsel,
            )
            .order_by(DiaryPrompt.id.desc())
            .first()
        )

        sig = signature_of(current_user.username, stu.username, dsel, "diary")
        is_signed = bool(sig and getattr(sig, "image", None))
        if is_signed:
            signed_count += 1

        sign_url = url_for("parent_sign", student=stu.username, date=dsel.isoformat())
        sig_preview = _upload_img(getattr(sig, "image", None) if sig else None, "parent-diary-signature", "家長簽名")
        sign_button = (
            f"<a class='btn btn-outline-secondary btn-sm' href='{sign_url}'>更新簽名</a>"
            if is_signed else
            f"<a class='btn btn-primary btn-sm' href='{sign_url}'>完成家長簽名</a>"
        )
        sign_status = (
            "<span class='parent-diary-chip parent-diary-chip--signed'>已簽名</span>"
            if is_signed else
            "<span class='parent-diary-chip parent-diary-chip--muted'>待簽名</span>"
        )

        student_name = escape(stu.display_name or stu.username)
        student_meta = escape(f"{stu.grade or ''}年{stu.class_no or ''}班")
        avatar_text = escape((stu.display_name or stu.username or "學")[0])

        if not prompt:
            cards.append(f"""
            <article class='parent-diary-card parent-diary-card--quiet'>
              <div class='parent-diary-card__aside'>
                <div class='parent-diary-avatar'>{avatar_text}</div>
                <div>
                  <div class='parent-diary-student'>{student_name}</div>
                  <div class='parent-diary-meta'>{student_meta}</div>
                </div>
              </div>
              <div class='parent-diary-card__main'>
                <div class='parent-diary-state parent-diary-state--empty'>今日尚未發布日記題目</div>
              </div>
            </article>
            """)
            continue

        prompted_count += 1
        sub = DiarySubmission.query.filter_by(prompt_id=prompt.id, student_name=stu.username).first()
        is_shared = bool(sub and getattr(sub, "share_to_parent", 0) == 1)
        if is_shared:
            shared_count += 1

        instruction = _safe_get(prompt, "instruction")
        teacher_name = escape(display_name_of(getattr(prompt, "created_by", "") or "") or "老師")
        created_txt = prompt.created_at.strftime("%Y-%m-%d %H:%M") if getattr(prompt, "created_at", None) else "—"
        prompt_img_html = _upload_img(diary_prompt_img_name(prompt), "parent-diary-photo", "日記題目附圖")
        prompt_panel = (
            "<section class='parent-diary-prompt-panel'>"
            "<div class='parent-diary-prompt-head'>"
            "<div>"
            "<div class='parent-diary-section-label'>老師題目</div>"
            f"<h3 class='parent-diary-title'>{escape(prompt.title or '（無標題）')}</h3>"
            "</div>"
            f"<div class='parent-diary-prompt-meta'>{teacher_name}<br>{created_txt}</div>"
            "</div>"
            f"{('<div class=\"parent-diary-prompt-note\">' + _nl_html(instruction) + '</div>') if instruction else '<div class=\"parent-diary-prompt-note parent-diary-prompt-note--empty\">老師沒有補充說明。</div>'}"
            f"{('<div class=\"parent-diary-photo-wrap\">' + prompt_img_html + '</div>') if prompt_img_html else ''}"
            "</section>"
        )

        submitted_txt = sub.created_at.strftime("%Y-%m-%d %H:%M") if (sub and getattr(sub, "created_at", None)) else "尚未繳交"
        if not sub:
            content_state = "<span class='parent-diary-chip parent-diary-chip--muted'>尚未繳交</span>"
            diary_body = (
                "<section class='parent-diary-locked'>"
                "<div class='parent-diary-section-label'>孩子日記</div>"
                "<div class='parent-diary-locked-title'>孩子尚未繳交這篇日記</div>"
                "<div class='parent-diary-muted'>您仍可先查看老師今天發布的題目與說明，待孩子完成後會在這裡顯示狀態。</div>"
                "</section>"
            )
        elif not is_shared:
            content_state = "<span class='parent-diary-chip parent-diary-chip--private'>孩子未公開</span>"
            diary_body = (
                "<section class='parent-diary-locked parent-diary-locked--private'>"
                "<div class='parent-diary-section-label'>孩子日記</div>"
                "<div class='parent-diary-locked-title'>孩子已繳交，尚未開放家長閱讀</div>"
                f"<div class='parent-diary-muted'>繳交時間：{submitted_txt}。目前先顯示老師題目與說明，日記內容會在孩子開放後出現。</div>"
                "</section>"
            )
        else:
            content_state = "<span class='parent-diary-chip parent-diary-chip--open'>可閱讀</span>"
            body = _safe_get(sub, "content", "body", "text", "answer")
            img_html = _upload_img(_safe_get(sub, "image", "photo", "picture"), "parent-diary-photo", "日記圖片")
            diary_text = _nl_html(body) if body else "孩子沒有填寫文字內容。"
            diary_body = (
                "<section class='parent-diary-entry'>"
                "<div class='parent-diary-entry-head'>"
                "<div>"
                "<div class='parent-diary-section-label'>孩子日記</div>"
                f"<div class='parent-diary-entry-title'>{student_name} 的回覆</div>"
                "</div>"
                f"<div class='parent-diary-entry-meta'>繳交時間<br>{submitted_txt}</div>"
                "</div>"
                "<div class='parent-diary-content'>"
                f"<article class='parent-diary-text'>{diary_text}</article>"
                f"{('<div class=\"parent-diary-photo-wrap\">' + img_html + '</div>') if img_html else ''}"
                "</div>"
                "</section>"
            )

        teacher_comment = _safe_get(sub, "teacher_comment", "teacher_feedback", "comment") if sub else ""
        teacher_comment_html = (
            "<div class='parent-diary-teacher-note'>"
            "<div class='parent-diary-section-label'>老師回饋</div>"
            f"<div>{_nl_html(teacher_comment)}</div>"
            "</div>"
            if teacher_comment else ""
        )
        sig_preview_html = (
            "<div class='parent-diary-sign-preview'>"
            "<div class='parent-diary-section-label'>簽名預覽</div>"
            f"{sig_preview}"
            "</div>"
            if sig_preview else ""
        )

        cards.append(f"""
        <article class='parent-diary-card'>
          <div class='parent-diary-card__aside'>
            <div class='parent-diary-avatar'>{avatar_text}</div>
            <div>
              <div class='parent-diary-student'>{student_name}</div>
              <div class='parent-diary-meta'>{student_meta}</div>
              <div class='parent-diary-status-stack'>{content_state}{sign_status}</div>
            </div>
          </div>
          <div class='parent-diary-card__main'>
            <div class='parent-diary-card__head'>
              <div>
                <div class='parent-diary-section-label'>今日紀錄</div>
                <h3 class='parent-diary-title'>{student_name} 的日記狀態</h3>
              </div>
              <div class='parent-diary-date'>{prompt.date}</div>
            </div>
            {prompt_panel}
            {diary_body}
            {teacher_comment_html}
            <div class='parent-diary-actions'>
              {sign_button}
              <a class='btn btn-outline-secondary btn-sm' href='/homework?date={dsel.isoformat()}'>回聯絡簿</a>
            </div>
            {sig_preview_html}
          </div>
        </article>
        """)

    nav = f"""
    <section class='parent-diary-hero'>
      <div>
        <div class='parent-diary-eyebrow'>家長日記</div>
        <h2>孩子的今日學習紀錄</h2>
      </div>
      <div class='parent-diary-hero__date'>
        <span>{dsel.strftime('%Y')}</span>
        <strong>{dsel.strftime('%m/%d')}</strong>
        <a href='/parent_diary?date={today_iso}'>回到今天</a>
      </div>
    </section>
    <section class='parent-diary-nav'>
      <a class='btn btn-sm btn-outline-secondary' href='/parent_diary?date={prev_d}'>&laquo; 前一天</a>
      <form method='get' class='parent-diary-date-form'>
        <input type='date' name='date' class='form-control form-control-sm' value='{dsel.isoformat()}'>
        <button class='btn btn-sm btn-primary'>前往</button>
      </form>
      <a class='btn btn-sm btn-outline-secondary' href='/parent_diary?date={next_d}'>下一天 &raquo;</a>
    </section>
    <section class='parent-diary-summary'>
      <div><strong>{len(cards)}</strong><span>綁定學童</span></div>
      <div><strong>{prompted_count}</strong><span>今日題目</span></div>
      <div><strong>{shared_count}</strong><span>可閱讀</span></div>
      <div><strong>{signed_count}</strong><span>已簽名</span></div>
    </section>
    """

    card_html = "".join(cards) if cards else "<div class='parent-diary-empty'>今天沒有可顯示的日記資料。</div>"
    inner = f"<div class='parent-diary-page'>{nav}<section class='parent-diary-list'>{card_html}</section></div>"
    return page("家長｜日記專區", inner)


@roles_required("leader")
@app.route("/leader/diary/delete/<int:pid>")
def leader_diary_delete(pid):
    p = DiaryPrompt.query.get_or_404(pid)
    if p.unit != (current_user.unit or ""):
        return toast_redirect("leader", "無權刪除此日記題目。", "warning")
    DiarySubmission.query.filter_by(prompt_id=pid).delete()
    db.session.delete(p)
    db.session.commit()
    return toast_redirect("leader", "已刪除日記題目與其所有提交。", "success")




if "SCHOOL_NAME" not in globals():
    SCHOOL_NAME = getattr(current_user, "unit", None) or "學校"

if "opt" not in globals():
    def opt(arr):
        return "".join(f"<option value='{x}'>{x}</option>" for x in arr)

if "is_staff_admin" not in globals():
    def is_staff_admin():
        return getattr(current_user, "role", "") in ("admin", "leader")

if "role_endpoint" not in globals():
    def role_endpoint(role: str) -> str:
        return {"admin": "teacher", "leader": "teacher", "teacher": "teacher",
                "student": "homework", "parent":"parent"}.get(role, "home")

if "scope_txt" not in globals():
    def scope_txt(t) -> str:
        try:
            if getattr(t, "is_school_wide", 0) == 1:
                return "全校"
            g = getattr(t, "grade", None)
            c = getattr(t, "class_no", None)
            if g and c: return f"{g}年{c}班"
            if g: return f"{g}年級"
            return "—"
        except Exception:
            return "—"

if "_desc_clean_html" not in globals():
    def _desc_clean_html(s: str) -> str:
        s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
        return _user_text_html(s)


if "save_image" not in globals():
    def save_image(file_storage):
        if not file_storage or not getattr(file_storage, "filename", "").strip():
            return None
        try:
            names = _save_images([file_storage], subdir="task")  
            return names[0] if names else None
        except Exception:
            try:
                from werkzeug.utils import secure_filename
                upload_dir = app.config.get("UPLOAD_FOLDER") or UPLOAD_DIR
                os.makedirs(upload_dir, exist_ok=True)
                ext = (file_storage.filename.rsplit(".", 1)[-1] if "." in file_storage.filename else "dat")
                fname = f"{uuid.uuid4().hex}.{ext}"
                path = os.path.join(upload_dir, secure_filename(fname))
                file_storage.save(path)
                return fname
            except Exception:
                return None


def _task_img_name(t):
    for key in ("image","attachment_image","image_name","image_file",
                "cover_image","photo","picture","img","mission_image","homework_image"):
        if hasattr(t, key):
            val = getattr(t, key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if hasattr(t, "images"):
        val = getattr(t, "images")
        if isinstance(val, list) and val:
            fst = val[0]
            if isinstance(fst, str) and fst.strip():
                return fst.strip()
        if isinstance(val, str) and val.strip():
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if parts:
                return parts[0]
    if hasattr(t, "extra"):
        try:
            data = json.loads(getattr(t, "extra"))
            for k in ["image","cover_image","picture","img"]:
                if k in data:
                    cand = data[k]
                    if isinstance(cand, str) and cand.strip():
                        return cand.strip()
                    if isinstance(cand, list) and cand:
                        return str(cand[0]).strip()
        except Exception:
            pass
    return None


def _ct_status(ct) -> str:
    p = _ct_load(ct)
    st = (p.get("status") or "").strip()
    if st in ("pending","approved","rejected"):
        return st
    if hasattr(ct, "approved"):
        return "pending" if (ct.approved is None) else ("approved" if ct.approved == 1 else "rejected")
    return "pending"

def _ct_reject_reason(ct) -> str:
    p = _ct_load(ct)
    if p.get("reject_reason"):
        return str(p.get("reject_reason"))
    return getattr(ct, "reject_reason", "") or ""

def _ct_created_at(ct):
    p = _ct_load(ct)
    iso = p.get("timestamp_iso")
    if iso:
        try:
            return datetime.fromisoformat(iso)
        except Exception:
            pass
    for name in ("timestamp","created_at","updated_at","approved_at"):
        if hasattr(ct, name) and getattr(ct, name):
            return getattr(ct, name)
    return datetime.utcnow()

def _ct_student_name(ct) -> str:
    
    u = _ct_user(ct)  
    if u:
        return u.username
    for name in ("student_name","username","created_by","user_name"):
        if hasattr(ct, name) and getattr(ct, name):
            return getattr(ct, name)
    if hasattr(ct, "user_id") and ct.user_id:
        u2 = User.query.get(ct.user_id)
        if u2:
            return u2.username
    return "student"

def _ct_task(ct):
    if hasattr(ct, "task") and getattr(ct, "task") is not None:
        return ct.task
    try:
        return Task.query.get(ct.task_id)
    except Exception:
        return None

def _ct_imgs(ct) -> list[str]:
    p = _ct_load(ct)
    out = []
    if isinstance(p.get("photos"), list):
        out += [x for x in p["photos"] if isinstance(x, str) and x.strip()]
    parent = p.get("parent_photo")
    if isinstance(parent, str) and parent.strip():
        out.append(parent)
    
    if hasattr(ct, "proof_image") and getattr(ct, "proof_image"):
        out.append(ct.proof_image)
    return out

def _count_words_chars(text: str) -> tuple[int, int]:
    s = (text or "").strip()
    chars = len(s)
    words = len([w for w in re.split(r"\s+", s) if w]) if s else 0
    return words, chars

@app.route("/teacher", methods=["GET", "POST"])
@roles_required("teacher", "leader", "admin")
def teacher():
    from datetime import datetime, timedelta  
    msg = ""
    unit = SCHOOL_NAME
    F = lambda k, d="": (request.form.get(k) or d).strip()

    
    def local_today():
        try:
            
            return (datetime.utcnow() + timedelta(hours=8)).date()
        except Exception:
            return datetime.now().date()

    
    def first_url(candidates: list[str], default_path: str = "#") -> str:
        for ep in candidates:
            try:
                return url_for(ep)
            except Exception:
                continue
        return default_path

    
    def get_teacher_assignments(username: str):
        func_ = globals().get("teacher_assignments")
        if callable(func_):
            try:
                return func_(username)
            except Exception:
                pass
        try:
            rows = TeachingAssignment.query.filter_by(teacher_username=username).all()
            return [{"subject": r.subject, "grade": r.grade, "class_no": r.class_no} for r in rows]
        except Exception:
            return []

    
    def _norm_assignments(raw):
        out = []
        for it in (raw or []):
            if isinstance(it, dict):
                s = it.get("subject"); g = it.get("grade"); c = it.get("class_no")
            elif isinstance(it, (list, tuple)) and len(it) >= 3:
                s, g, c = it[0], it[1], it[2]
            else:
                continue
            if s and g and c:
                out.append((str(s), str(g), str(c)))
        return out

    
    combos = []
    if current_user.role in ("leader", "admin"):
        for grd in GRADE_OPTIONS:
            for cls in CLASS_OPTIONS:
                for subj in CATEGORY_OPTIONS:
                    combos.append((str(grd), str(cls), str(subj)))
    else:
        if str(getattr(current_user, "is_homeroom", 0) or 0) == "1" and current_user.grade and current_user.class_no:
            for subj in CATEGORY_OPTIONS:
                combos.append((str(current_user.grade), str(current_user.class_no), str(subj)))
        for subj, grd, cls in _norm_assignments(get_teacher_assignments(current_user.username)):
            combos.append((str(grd), str(cls), str(subj)))

    seen, uniq = set(), []
    for grd, cls, subj in combos:
        key = (grd, cls, subj)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(key)

    def _cat_index(x):
        try:
            return CATEGORY_OPTIONS.index(x)
        except Exception:
            return 999

    def _to_int(s, default=999):
        try:
            return int(s)
        except Exception:
            return default

    uniq.sort(key=lambda x: (_to_int(x[0]), _to_int(x[1]), _cat_index(x[2])))
    combo_opts = "".join(
        f"<option value='{grd}|{cls}|{subj}'>{grd}年{cls}班 ／ {subj}</option>"
        for (grd, cls, subj) in uniq
    )

    def teacher_action_list(items: list[tuple[str, str, str]]) -> str:
        if not items:
            return ""
        return (
            "<div class='teacher-action-list'>"
            + "".join(
                f"<a class='btn btn-sm btn-outline-{style}' href='{href}'>{label}</a>"
                for label, href, style in items
            )
            + "</div>"
        )

    def teacher_panel(label: str, title: str, body: str, actions: str = "", extra_class: str = "") -> str:
        return (
            f"<section class='teacher-panel {extra_class}'>"
            "<div class='teacher-panel__head'>"
            f"<div><div class='teacher-section-label'>{escape(label)}</div><h3>{escape(title)}</h3></div>"
            f"{actions}"
            "</div>"
            f"{body}"
            "</section>"
        )

    def teacher_metric(label: str, value: str, hint: str = "") -> str:
        hint_html = f"<span>{escape(hint)}</span>" if hint else ""
        return f"<div class='teacher-metric'><strong>{escape(value)}</strong><small>{escape(label)}</small>{hint_html}</div>"

    def teacher_progress(label: str, value_text: str, percent: int, color_class: str = "") -> str:
        pct = max(0, min(100, int(percent or 0)))
        color = f" {color_class}" if color_class else ""
        return (
            "<div class='teacher-progress'>"
            "<div class='teacher-progress__top'>"
            f"<span>{escape(label)}</span><strong>{escape(value_text)}</strong>"
            "</div>"
            "<div class='progress' style='height: 10px;'>"
            f"<div class='progress-bar{color}' role='progressbar' style='width: {pct}%;' "
            f"aria-valuenow='{pct}' aria-valuemin='0' aria-valuemax='100'></div>"
            "</div>"
            "</div>"
        )

    def can_post(grade, class_no, subj):
        return (str(grade), str(class_no), str(subj)) in uniq

    
    goal_msg = ""  

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        
        if action == "create_task":
            title   = F("title")
            content = F("content")
            combo   = F("combo")
            
            start   = parse_date(F("publish_date")) or local_today()

            
            end_raw = F("end_date")
            end     = parse_date(end_raw)

            img_file  = request.files.get("image")
            saved_img = save_image(img_file) if (img_file and getattr(img_file, "filename", "").strip()) else None

            if not combo:
                msg = "請選擇班級與科目。"
            elif not content and not title:
                msg = "請輸入標題或內文（至少一項）。"
            elif not end_raw or not end:
                msg = "請選擇截止日（不得留空）。"
            else:
                try:
                    grade_, class_no_, subj_ = combo.split("|", 3)
                except Exception:
                    grade_, class_no_, subj_ = "", "", ""

                if current_user.role not in ("leader", "admin") and not can_post(grade_, class_no_, subj_):
                    return toast_redirect("teacher", "您無權對此班級/科目發佈作業。", "warning")

                title = (title or (content[:50] or f"{subj_}作業")).strip()
                desc  = content or ""

                t = Task(
                    title=title,
                    description=desc,
                    created_by=current_user.username,
                    category=subj_,
                    mission_category=None,
                    points=0,
                    start_date=start,
                    end_date=end,
                    unit=unit,
                    grade=grade_,
                    class_no=class_no_,
                    is_school_wide=0,
                    task_type="homework",
                    is_view_only=1,
                )
                _set_task_image_if_possible(t, saved_img)
                db.session.add(t)
                db.session.commit()
                return toast_redirect("teacher", "已新增作業", "success")

        
        elif action == "update_reading_goal":
            wg_raw = F("week_goal")   
            mg_raw = F("month_goal")
            tg_raw = F("term_goal")
            sg_raw = F("sdg_target")

            ok = True

            try:
                wg_val = max(0, int(wg_raw or "0"))
            except Exception:
                ok = False
                goal_msg += "本週班級總目標請輸入數字。"
                wg_val = globals().get("READING_WEEK_GOAL", 300)

            try:
                mg_val = max(0, int(mg_raw or "0"))
            except Exception:
                ok = False
                if goal_msg:
                    goal_msg += " "
                goal_msg += "本月目標請輸入數字。"
                mg_val = globals().get("READING_MONTH_GOAL", 1200)

            try:
                tg_val = max(0, int(tg_raw or "0"))
            except Exception:
                ok = False
                if goal_msg:
                    goal_msg += " "
                goal_msg += "本學期目標請輸入數字。"
                tg_val = globals().get("READING_TERM_GOAL", 3000)

            try:
                sg_val = max(0, int(sg_raw or "0"))
            except Exception:
                ok = False
                if goal_msg:
                    goal_msg += " "
                goal_msg += "班級每項 SDG 平均目標請輸入數字。"
                sg_val = globals().get("SDG_CLASS_AVG_TARGET", 10)

            if ok:
                globals()["READING_WEEK_GOAL"]    = wg_val
                globals()["READING_MONTH_GOAL"]   = mg_val
                globals()["READING_TERM_GOAL"]    = tg_val
                globals()["SDG_CLASS_AVG_TARGET"] = sg_val
                return toast_redirect("teacher", "閱讀目標已更新。", "success")

    
    def _goal_value(name, default):
        v = globals().get(name, default)
        try:
            return int(v)
        except Exception:
            return default

    week_goal_val  = _goal_value("READING_WEEK_GOAL", 300)   
    month_goal_val = _goal_value("READING_MONTH_GOAL", 1200)
    term_goal_val  = _goal_value("READING_TERM_GOAL", 3000)
    sdg_target_val = _goal_value("SDG_CLASS_AVG_TARGET", 10)
    reading_block = ""
    diary_block   = ""

    cls_unit  = getattr(current_user, "unit", unit) or unit
    cls_grade = (str(getattr(current_user, "grade", "") or "")).strip()
    cls_class = (str(getattr(current_user, "class_no", "") or "")).strip()

    def _teacher_review_notice() -> str:
        reading_pending = 0
        coread_waiting = 0
        leave_pending = 0
        med_pending = 0
        notice_items = []

        try:
            reading_recs = (
                CompletedTask.query
                .join(Task, CompletedTask.task_id == Task.id)
                .filter(Task.task_type == "mission")
                .order_by(CompletedTask.id.desc())
                .all()
            )
            for ct in reading_recs:
                stu = _ct_user(ct)
                if not stu or not can_manage_reading_class(current_user, getattr(stu, "grade", None), getattr(stu, "class_no", None)):
                    continue
                payload = _ct_load(ct) or {}
                if _ct_status(ct) != "pending":
                    continue
                if reading_need_parent_coread(ct=ct, payload=payload, task=getattr(ct, "task", None)) and not reading_parent_coread_done(payload):
                    coread_waiting += 1
                    continue
                reading_pending += 1
                if len(notice_items) < 3:
                    notice_items.append(f"{display_name_of(stu.username)}的閱讀認證等待審核")
        except Exception:
            reading_pending = 0
            coread_waiting = 0

        try:
            leave_q = LeaveRequest.query.filter(LeaveRequest.status.is_(None))
            if current_user.role in ("leader", "admin"):
                leave_q = leave_q.filter(LeaveRequest.unit == cls_unit)
            elif is_homeroom_of(current_user, cls_grade, cls_class):
                leave_q = leave_q.filter(
                    LeaveRequest.unit == cls_unit,
                    LeaveRequest.grade == cls_grade,
                    LeaveRequest.class_no == cls_class,
                )
            else:
                leave_q = leave_q.filter(text("1=0"))
            leave_rows = leave_q.order_by(LeaveRequest.created_at.desc()).limit(4).all()
            leave_pending = leave_q.count()
            for lr in leave_rows:
                if len(notice_items) >= 3:
                    break
                notice_items.append(f"{lr.student_display or lr.student_name}的{lr.leave_type}尚未審核")
        except Exception:
            leave_pending = 0

        try:
            med_q = MedicationRecord.query.filter(
                MedicationRecord.date == local_today(),
                MedicationRecord.status == "pending",
            )
            if current_user.role in ("leader", "admin"):
                med_q = med_q.filter(MedicationRecord.unit == cls_unit)
            elif is_homeroom_of(current_user, cls_grade, cls_class):
                med_q = med_q.filter(
                    MedicationRecord.unit == cls_unit,
                    MedicationRecord.grade == cls_grade,
                    MedicationRecord.class_no == cls_class,
                )
            else:
                med_q = med_q.filter(text("1=0"))
            med_pending = med_q.count()
        except Exception:
            med_pending = 0

        active_total = reading_pending + leave_pending + med_pending
        passive_total = coread_waiting
        if active_total <= 0:
            return ""

        title = f"有 {active_total} 筆待處理事項"
        if active_total and passive_total:
            desc = f"另有 {passive_total} 筆親子共讀等待家長補寫，完成後會進入審核。"
        else:
            desc = "建議先處理待審項目，讓學生與家長可以即時看到結果。"

        item_html = ""
        if notice_items:
            item_html = (
                "<div class='teacher-review-notice__items'>"
                + "".join(f"<span>{escape(item)}</span>" for item in notice_items)
                + "</div>"
            )

        active_class = " teacher-review-notice--active"
        return f"""
        <section class='teacher-review-notice{active_class}'>
          <div class='teacher-review-notice__main'>
            <div class='teacher-section-label'>待辦提醒</div>
            <h3>{escape(title)}</h3>
            <p>{escape(desc)}</p>
            {item_html}
          </div>
          <div class='teacher-review-notice__grid'>
            <a href='{url_for("reading_review_queue")}'><strong>{reading_pending}</strong><span>閱讀認證待審</span></a>
            <a href='{url_for("reading_review_queue")}'><strong>{coread_waiting}</strong><span>親子共讀待補</span></a>
            <a href='{url_for("leave_manage")}'><strong>{leave_pending}</strong><span>請假待審</span></a>
            <a href='{url_for("medication_records")}'><strong>{med_pending}</strong><span>今日用藥待處理</span></a>
          </div>
        </section>
        """

    if cls_grade and cls_class:
        
        students = User.query.filter_by(
            unit=cls_unit,
            grade=cls_grade,
            class_no=cls_class,
            role="student",
        ).all()
        stu_ids = {s.id for s in students}
        class_size = len(students)

        
        today_d = local_today()
        ws, we = _week_range(today_d)

        from sqlalchemy import func
        col = ct_time_col()
        q = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(
            Task.task_type == "mission"
        )
        if col is not None:
            q = q.filter(
                func.date(col) >= ws.isoformat(),
                func.date(col) <= we.isoformat(),
            )
        cts = q.all()

        approved_students = set()
        total_score = 0
        total_approved = 0
        sdg_counter = defaultdict(int)
        coread_total_records = 0
        coread_with_parent = 0
        coread_full_bonus = 0
        per_stu_coread = defaultdict(lambda: dict(any=0, full=0))

        for ct in cts:
            u = _ct_user(ct)
            if not u or u.id not in stu_ids:
                continue
            if _ct_status(ct) != "approved":
                continue
            p = _ct_load(ct)
            approved_students.add(u.id)
            total_approved += 1
            total_score += _safe_int(p.get("score_total", 0), 0)

            codes = []
            if p.get("sdg_codes"):
                codes = p["sdg_codes"]
            elif p.get("sdg_code"):
                codes = [p["sdg_code"]]
            used_codes = set()
            for raw_code in codes:
                code = _safe_int(raw_code, 0)
                if code <= 0 or code in used_codes:
                    continue
                used_codes.add(code)
                sdg_counter[code] += 1

            coread_total_records += 1
            parent_coread = reading_parent_coread_done(p)
            full_coread = parent_coread
            if parent_coread:
                coread_with_parent += 1
                per_stu_coread[u.id]["any"] += 1
            if full_coread:
                coread_full_bonus += 1
                per_stu_coread[u.id]["full"] += 1

        completion_rate = (
            f"{(len(approved_students) * 100 / class_size):.0f}%" if class_size else "—"
        )
        avg_score = f"{(total_score / total_approved):.1f}" if total_approved else "0.0"

        per_week_pts = {}
        sum_week_pts = 0
        count_any = 0
        count_goal = 0
        student_week_target = math.ceil(week_goal_val / class_size) if class_size and week_goal_val > 0 else 0
        for stu in students:
            pts, _sdg_set = _user_week_points(stu.id, ws, we)
            pts = int(pts or 0)
            per_week_pts[stu.id] = pts
            sum_week_pts += pts
            if pts > 0:
                count_any += 1
            if student_week_target > 0 and pts >= student_week_target:
                count_goal += 1
        class_avg_week = round(sum_week_pts / class_size, 1) if class_size else 0.0
        any_rate = f"{(count_any * 100 / class_size):.0f}%" if class_size else "—"
        goal_rate = f"{(count_goal * 100 / class_size):.0f}%" if class_size and student_week_target > 0 else "—"
        any_pct_val = int(count_any * 100 / class_size) if class_size else 0
        goal_pct_val = int(count_goal * 100 / class_size) if class_size and student_week_target > 0 else 0

        if coread_total_records > 0:
            coread_rate = round(coread_with_parent * 100 / coread_total_records, 1)
            coread_full_rate = round(coread_full_bonus * 100 / coread_total_records, 1)
        else:
            coread_rate = coread_full_rate = 0.0

        coread_top = sorted(
            [(per_stu_coread[stu.id]["any"], stu) for stu in students if per_stu_coread[stu.id]["any"] > 0],
            key=lambda item: item[0],
            reverse=True,
        )[:5]
        if coread_top:
            coread_top_html = (
                "<div class='teacher-inline-list'>"
                + "".join(
                    "<div class='teacher-inline-row'>"
                    f"<strong>{escape(stu.display_name or stu.username)}</strong>"
                    f"<span>{cnt} 次親子共讀</span>"
                    "</div>"
                    for cnt, stu in coread_top
                )
                + "</div>"
            )
        else:
            coread_top_html = "<div class='teacher-empty teacher-empty--compact'><strong>本週尚無親子共讀紀錄</strong><span>有資料後會在這裡顯示孩子參與情形。</span></div>"

        max_sdg_count = max(sdg_counter.values()) if sdg_counter else 0
        sorted_sdg = sorted([(code, sdg_counter.get(code, 0)) for code, _name in SDG_OPTIONS], key=lambda item: item[1])
        weak_sdg = {code for code, _count in sorted_sdg[:4]} if max_sdg_count > 0 else set()

        def sdg_heat_cell(code: int, name: str) -> str:
            count = sdg_counter.get(code, 0)
            bg = "#f8fbf6"
            if max_sdg_count > 0 and count > 0:
                intensity = int((count / max_sdg_count) * 100)
                bg = f"hsl(138, 44%, {92 - intensity // 3}%)"
            weak_badge = "<span class='badge bg-warning text-dark ms-1'>待補強</span>" if code in weak_sdg else ""
            return (
                f"<div class='teacher-sdg-cell' style='background:{bg};'>"
                f"<span>{code:02d} {escape(name)}</span>"
                f"<strong>{count}</strong>{weak_badge}"
                "</div>"
            )

        sdg_cells = "".join(sdg_heat_cell(code, name) for code, name in SDG_OPTIONS)

        class_rows = []
        weak_rows = []
        for stu in students:
            pts = per_week_pts.get(stu.id, 0)
            detail_url = url_for("reading_teacher_student", student_username=stu.username)
            student_name_link = (
                f"<a class='teacher-student-link' href='{detail_url}'>"
                f"{escape(stu.display_name or stu.username)}</a>"
            )
            if student_week_target > 0:
                goal_badge = "<span class='badge bg-success'>已達標</span>" if pts >= student_week_target else "<span class='badge bg-secondary'>未達標</span>"
            else:
                goal_badge = "<span class='badge bg-light text-muted border'>未設定</span>"
            if student_week_target > 0 and pts < student_week_target:
                weak_rows.append(
                    "<tr>"
                    f"<td>{student_name_link}</td>"
                    f"<td class='text-end'>{pts}</td>"
                    f"<td class='text-end'><a class='teacher-table-action' href='{detail_url}'>查看</a></td>"
                    "</tr>"
                )
            class_rows.append(
                "<tr>"
                f"<td>{student_name_link}</td>"
                f"<td class='text-end'>{pts}</td>"
                f"<td class='text-center'>{goal_badge}</td>"
                f"<td class='text-end'>{per_stu_coread[stu.id]['any']}</td>"
                f"<td class='text-end'><a class='teacher-table-action' href='{detail_url}'>查看</a></td>"
                "</tr>"
            )

        class_table_html = (
            "<div class='table-responsive teacher-inline-table'>"
            "<table class='table table-sm align-middle mb-0'>"
            "<thead><tr><th>學生</th><th class='text-end'>本週分數</th><th class='text-center'>狀態</th><th class='text-end'>親子共讀</th><th class='text-end'>個別概況</th></tr></thead>"
            f"<tbody>{''.join(class_rows)}</tbody></table></div>"
            if class_rows
            else "<div class='teacher-empty teacher-empty--compact'><strong>目前沒有學生帳號</strong><span>建立學生後即可顯示閱讀一覽。</span></div>"
        )
        if weak_rows:
            weak_table_html = (
                "<div class='table-responsive teacher-inline-table'>"
                "<table class='table table-sm align-middle mb-0'>"
                "<thead><tr><th>學生</th><th class='text-end'>本週分數</th><th class='text-end'>個別概況</th></tr></thead>"
                f"<tbody>{''.join(weak_rows)}</tbody></table></div>"
            )
        elif student_week_target > 0:
            weak_table_html = "<div class='teacher-empty teacher-empty--compact'><strong>本週都已達標</strong><span>目前沒有需要特別提醒的學生。</span></div>"
        else:
            weak_table_html = "<div class='teacher-empty teacher-empty--compact'><strong>尚未設定目標</strong><span>設定班級共同目標後，系統會換算每位學生的參考目標。</span></div>"

        teacher_dashboard_inline = f"""
        <div class='teacher-reading-dashboard-inline'>
          <div class='teacher-subsection-title'>完整閱讀儀表板</div>
          <div class='teacher-metric-list teacher-metric-list--reading'>
            {teacher_metric("本週有閱讀認證", f"{count_any} / {class_size}", any_rate)}
            {teacher_metric("達成換算目標", f"{count_goal} / {class_size}", goal_rate)}
            {teacher_metric("班級本週平均", f"{class_avg_week:.1f}", "分 / 人")}
            {teacher_metric("親子共讀比例", f"{coread_rate:.1f}%", f"完整心得 {coread_full_bonus} 筆")}
          </div>
          <div class='teacher-progress-stack'>
            {teacher_progress("本週有閱讀認證比例", f"{any_rate}", any_pct_val, "bg-info")}
            {teacher_progress("達成換算目標比例", f"{goal_rate}", goal_pct_val, "bg-success")}
          </div>
          <div class='teacher-reading-dashboard-grid'>
            <section class='teacher-reading-subcard'>
              <div class='teacher-reading-subcard-title'>親子共讀概況</div>
              <div class='teacher-note'>本週有親子共讀的閱讀紀錄 {coread_with_parent} 筆，完整家長心得 {coread_full_bonus} 筆，完整率約 {coread_full_rate:.1f}%。</div>
              {coread_top_html}
            </section>
            <section class='teacher-reading-subcard'>
              <div class='teacher-reading-subcard-title'>本週未達換算目標</div>
              <div class='teacher-note'>班級共同目標 {week_goal_val} 分，換算每位學生約 {student_week_target or 0} 分。</div>
              {weak_table_html}
            </section>
            <section class='teacher-reading-subcard teacher-reading-subcard--wide'>
              <div class='teacher-reading-subcard-title'>班級學生本週閱讀一覽</div>
              {class_table_html}
            </section>
            <section class='teacher-reading-subcard teacher-reading-subcard--wide teacher-reading-subcard--heatmap'>
              <div class='teacher-reading-subcard-title'>SDG 熱力圖</div>
              <div class='teacher-note'>依本週已通過閱讀認證統計，顏色越深代表該 SDG 主題出現越多。</div>
              <div class='teacher-sdg-grid'>{sdg_cells}</div>
            </section>
          </div>
        </div>
        """

        
        target_total = week_goal_val
        if target_total > 0:
            progress = min(100, int(round(total_score * 100 / target_total)))
            remain   = max(0, target_total - total_score)
            target_text = (
                f"本週全班累積 {total_score} 分／班級共同目標 {target_total} 分，約 {progress}%，"
                f"距離目標約還差 {remain} 分。"
            )
        else:
            progress = 0
            target_text = "尚未設定本週班級共同目標，或班級人數為 0。"

        review_url = "/reading/review"
        goal_msg_html = (
            f"<div class='alert alert-warning mb-2'>{goal_msg}</div>" if goal_msg else ""
        )

        reading_block = f"""
        <div id='teacher-reading-overview' class='card border-0 shadow-sm mb-4 teacher-reading-card'>
          <div class='card-body'>
            <div class='d-flex justify-content-between align-items-center mb-2'>
              <div>
                <h5 class='card-title mb-0'>本週班級閱讀概況</h5>
                <div class='small text-muted'>{cls_unit} · {cls_grade}年{cls_class}班 · {ws} ~ {we}</div>
              </div>
              <div class='btn-group'>
                <a class='btn btn-sm btn-outline-success' href='/reading/tasks'>永續閱讀任務</a>
                <a class='btn btn-sm btn-outline-success' href='{review_url}'>閱讀認證審核</a>
              </div>
            </div>

            <div class='teacher-reading-summary-grid'>
              <div class='teacher-reading-summary-card'>
                <span>本週有認證學生</span>
                <strong>{len(approved_students)} / {class_size}</strong>
                <small>完成率約 {completion_rate}</small>
              </div>
              <div class='teacher-reading-summary-card'>
                <span>本週平均得分</span>
                <strong>{avg_score}</strong>
                <small>僅計入已核准的閱讀認證</small>
              </div>
              <div class='teacher-reading-summary-card'>
                <span>班級人數</span>
                <strong>{class_size}</strong>
                <small>本週班級共同目標：{week_goal_val} 分</small>
              </div>
            </div>

            <div class='mb-2'>
              <div class='d-flex justify-content-between small'>
                <span>全班本週累積分數進度</span>
                <span>{progress}%</span>
              </div>
              <div class='progress' style='height: 10px;'>
                <div class='progress-bar' role='progressbar'
                     style='width: {progress}%;'
                     aria-valuenow='{progress}' aria-valuemin='0' aria-valuemax='100'></div>
              </div>
              <div class='small text-muted mt-1'>{target_text}</div>
            </div>

            <hr class='my-3'>

            <h6 class='mb-2'>閱讀目標設定</h6>
            {goal_msg_html}
            <form method='post' class='row g-2'>
              <input type='hidden' name='action' value='update_reading_goal'>
              <div class='col-md-3'>
                <label class='form-label'>本週班級總目標分數</label>
                <input type='number' name='week_goal' min='0' step='5' value='{week_goal_val}' class='form-control'>
              </div>
              <div class='col-md-3'>
                <label class='form-label'>本月每生目標分數</label>
                <input type='number' name='month_goal' min='0' step='10' value='{month_goal_val}' class='form-control'>
              </div>
              <div class='col-md-3'>
                <label class='form-label'>本學期每生目標分數</label>
                <input type='number' name='term_goal' min='0' step='10' value='{term_goal_val}' class='form-control'>
              </div>
              <div class='col-md-3'>
                <label class='form-label'>班級每項 SDG 平均目標</label>
                <input type='number' name='sdg_target' min='0' step='1' value='{sdg_target_val}' class='form-control'>
              </div>
              <div class='col-12 text-end mt-2'>
                <button class='btn btn-sm btn-primary'>儲存閱讀目標</button>
              </div>
            </form>
            {teacher_dashboard_inline}
          </div>
        </div>
        """

        
        today_str = today_d.isoformat()

        today_prompt = (
            DiaryPrompt.query
            .filter(
                DiaryPrompt.grade == cls_grade,
                DiaryPrompt.class_no == cls_class,
                DiaryPrompt.date == today_str,
            )
            .order_by(DiaryPrompt.id.desc())
            .first()
        )

        diary_link = first_url(["teacher_diary"], "/teacher_diary")
        diary_overview_link = first_url(
            ["diary_overview", "teacher_diary_overview", "diary_class_overview"],
            "/teacher_day",
        )

        if today_prompt:
            
            prompt_title = getattr(today_prompt, "title", None) or "（未命名題目）"

            
            prompt_desc = (
                getattr(today_prompt, "description", None)
                or getattr(today_prompt, "content", None)
                or getattr(today_prompt, "text", None)
                or getattr(today_prompt, "prompt", None)
                or ""
            )

            
            subs_q = DiarySubmission.query.filter_by(prompt_id=today_prompt.id)
            submissions = subs_q.all()
            submitted = len(submissions)
            submit_percent = int((submitted * 100 / class_size) if class_size else 0)

            
            signed_count = 0
            signed_percent = 0
            try:
                ps_model = ParentSignature
                
                student_usernames = {
                    str(getattr(s, "username"))
                    for s in students
                    if getattr(s, "username", None)
                }
                if student_usernames:
                    sig_q = ps_model.query.filter(ps_model.date == today_d)
                    if hasattr(ps_model, "scope"):
                        sig_q = sig_q.filter(ps_model.scope == "homework")
                    sig_rows = sig_q.all()

                    signed_students = set()
                    for sig in sig_rows:
                        name = getattr(sig, "student_name", None)
                        if name and str(name) in student_usernames:
                            signed_students.add(str(name))
                    signed_count = len(signed_students)
            except Exception:
                signed_count = 0

            if class_size:
                signed_percent = int((signed_count * 100 / class_size))
            else:
                signed_percent = 0

            
            if prompt_desc:
                desc_html = (
                    "<div class='mt-2 fs-6 text-muted'>"
                    f"{prompt_desc}"
                    "</div>"
                )
            else:
                desc_html = ""
            prompt_image_html = img_html(diary_prompt_img_name(today_prompt), maxw=260)

            diary_actions = teacher_action_list([
                ("管理日記題目", diary_link, "secondary"),
                ("班級日記總覽", diary_overview_link, "dark"),
            ])
            diary_body = (
                f"<div class='teacher-meta'>{today_str}</div>"
                "<div class='teacher-focus-card'>"
                "<span class='badge bg-primary'>題目</span>"
                f"<strong>{escape(prompt_title)}</strong>"
                f"{desc_html}"
                f"{prompt_image_html}"
                "</div>"
                "<div class='teacher-progress-stack'>"
                + teacher_progress("已繳交", f"{submitted} / {class_size} · {submit_percent}%", submit_percent)
                + teacher_progress("已簽名", f"{signed_count} / {class_size} · {signed_percent}%", signed_percent, "bg-success")
                + "</div>"
                "<div class='teacher-note'>已簽名統計來自家長於今日針對作業所做的電子簽名。</div>"
            )
            diary_block = teacher_panel("今日班級日記", "日記繳交與簽閱概況", diary_body, diary_actions, "teacher-panel--diary")
        else:
            diary_actions = teacher_action_list([
                ("前往建立今日題目", diary_link, "secondary"),
                ("班級日記總覽", diary_overview_link, "dark"),
            ])
            diary_body = (
                f"<div class='teacher-empty'>"
                f"<strong>{today_str}</strong>"
                "<span>今日尚未設定日記題目。</span>"
                "</div>"
            )
            diary_block = teacher_panel("今日班級日記", "日記繳交與簽閱概況", diary_body, diary_actions, "teacher-panel--diary")
    else:
	        
        reading_block = teacher_panel(
            "閱讀概況",
            "班級閱讀概況",
            "<div class='teacher-empty'><strong>尚未設定導師班級</strong><span>目前無法顯示班級閱讀與日記概況。</span></div>",
            "",
            "teacher-panel--reading",
        )
        diary_block = ""

    
    if current_user.role in ("leader", "admin"):
        rows = db.session.execute(
            text(
                """
            SELECT id, title, description, category, grade, class_no, start_date, end_date, created_by, image
              FROM task
             WHERE unit = :unit
               AND task_type = 'homework'
             ORDER BY COALESCE(end_date, start_date) DESC, id DESC
        """
            ),
            {"unit": unit},
        ).mappings().all()
    else:
        conds, params = [], {"unit": unit}
        if str(getattr(current_user, "is_homeroom", 0) or 0) == "1" and current_user.grade and current_user.class_no:
            conds.append("(task.grade = :hg AND task.class_no = :hc)")
            params["hg"], params["hc"] = current_user.grade, current_user.class_no
        assigns = _norm_assignments(get_teacher_assignments(current_user.username))
        for i, (subj, grd, cls) in enumerate(assigns):
            params[f"s{i}"], params[f"g{i}"], params[f"c{i}"] = subj, grd, cls
            conds.append(f"(task.category = :s{i} AND task.grade = :g{i} AND task.class_no = :c{i})")
        where_sql = " OR ".join(conds) or "1=0"
        rows = db.session.execute(
            text(
                f"""
            SELECT id, title, description, category, grade, class_no, start_date, end_date, created_by, image
              FROM task
             WHERE unit = :unit
               AND task_type = 'homework'
               AND ({where_sql})
             ORDER BY COALESCE(end_date, start_date) DESC, id DESC
        """
            ),
            params,
        ).mappings().all()

    
    def scope_txt_row(r) -> str:
        return f"{r['grade']}年{r['class_no']}班"

    def tli_row(r):
        end_txt = "未設定截止日" if not r["end_date"] else f"截止：{r['end_date']}"
        can_btn = (current_user.role in ("leader", "admin")) or (r.get("created_by") == current_user.username)
        desc = (r.get("description") or "").strip()
        desc = (desc[:80] + "…") if len(desc) > 80 else desc
        desc_html = f"<p>{escape(desc)}</p>" if desc else ""
        thumb = img_html(r.get("image"), maxw=220) if r.get("image") else ""
        right_html = ""
        if can_btn:
            right_html = (
                "<div class='teacher-task-actions'>"
                f"<a class='btn btn-sm btn-outline-warning' href='/edit_task/{r['id']}'>修改</a>"
                f"<a class='btn btn-sm btn-outline-danger' href='/delete_task/{r['id']}'>刪除</a>"
                "</div>"
            )
        return (
            "<article class='teacher-task-card'>"
            "<div class='teacher-task-card__main'>"
            f"<div class='teacher-task-title'>{escape(r['title'] or '未命名作業')}</div>"
            f"{desc_html}"
            f"{thumb}"
            "<div class='teacher-task-meta'>"
            f"<span>{escape(scope_txt_row(r))}</span>"
            f"<span>{escape(r['category'] or '其他')}</span>"
            f"<span>{escape(end_txt)}</span>"
            "</div>"
            "</div>"
            f"{right_html}"
            "</article>"
        )

    rows_html = (
        f"<div class='teacher-task-list'>{''.join(map(tli_row, rows))}</div>"
        if rows
        else "<div class='teacher-empty'><strong>尚無作業</strong><span>目前沒有可管理的作業。</span></div>"
    )
    tasks_block = teacher_panel("作業清單", "我能管理的作業", rows_html, "", "teacher-panel--tasks")

    
    _g = (str(getattr(current_user, "grade", "") or "")).strip()
    _c = (str(getattr(current_user, "class_no", "") or "")).strip()
    scope_text = f"{cls_unit or ''} · {_g}年{_c}班" if (_g and _c) else (cls_unit or "")
    header = (
        "<section class='teacher-hero'>"
        "<div>"
        "<div class='teacher-section-label'>老師工作區</div>"
        f"<h2>你好，{escape(current_user.display_name or current_user.username)}</h2>"
        f"<span>{escape(scope_text)}</span>"
        "</div>"
        "</section>"
    )

    toolbar = (
        "<section class='teacher-toolbar'>"
        "<a class='btn btn-outline-secondary btn-sm' href='/profile'>個人檔案</a>"
        f"<a class='btn btn-outline-primary btn-sm' href='{first_url(['comm','comm_list','comm_notes','comm_index','teacher_comm'],'/comm')}'>親師交流</a>"
        "<a class='btn btn-outline-secondary btn-sm' href='/medication'>用藥紀錄</a>"
        f"<a class='btn btn-outline-secondary btn-sm' href='{first_url(['leave','teacher_leave','leave_index'],'/leave/manage')}'>請假</a>"
        "</section>"
    )

    
    _today_iso = local_today().isoformat()
    form_body = (
        "<form method='post' class='teacher-form-stack' enctype='multipart/form-data'>"
        "<input type='hidden' name='action' value='create_task'>"
        "<div>"
        "<label class='form-label'>班級與科目</label>"
        f"<select name='combo' class='form-select form-select-lg'>{combo_opts or '<option value=\"\">（尚無可發佈範圍，請先設定授課指派）</option>'}</select>"
        "</div>"
        "<div>"
        "<label class='form-label'>發布日</label>"
        f"<input type='date' name='publish_date' class='form-control form-control-lg' value='{_today_iso}' required></div>"
        "<div><label class='form-label'>截止日</label>"
        f"<input type='date' name='end_date' class='form-control form-control-lg' value='{_today_iso}' required></div>"
        "<div>"
        "<label class='form-label'>標題</label>"
        "<input name='title' class='form-control form-control-lg' placeholder='例如：本週國語作業' maxlength='200'>"
        "</div>"
        "<div>"
        "<label class='form-label'>作業內容</label>"
        "<textarea name='content' class='form-control' rows='4' placeholder='請輸入作業內容'></textarea>"
        "</div>"
        "<div><label class='form-label'>附圖（可留空）</label>"
        "<input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"
        f"<button class='btn btn-primary btn-lg' {'disabled' if not combo_opts else ''}>新增作業</button>"
        "</form>"
    )
    form = teacher_panel("發布作業", "新增班級作業", form_body, "", "teacher-panel--publish")

    
    announce_link = first_url(["teacher_announce"], "/announce")
    announce_block = teacher_panel(
        "公告",
        "公告管理",
        "<div class='teacher-note'>集中查看與管理班級公告。</div>",
        teacher_action_list([("前往公告管理", announce_link, "primary")]),
        "teacher-panel--announce",
    )

    
    content = (
        "<div class='teacher-page'>"
        + header
        + _teacher_review_notice()
        + toolbar
        + form
        + tasks_block
        + diary_block
        + announce_block
        + reading_block
        + "</div>"
    )

    return page("老師工作區", content, msg=msg)

@app.route("/signatures")
@roles_required("teacher", "leader", "admin")
def signature_manage():
    """
    簽名管理頁面：
    - 顯示簽名紀錄列表（依照建立時間由新到舊）
    - 可預覽簽名圖片
    - 可刪除紀錄
    """

    
    def _get_signature_model():
        
        for name in ("SignatureRecord", "ParentSignature", "Signature", "SignRecord"):
            m = globals().get(name)
            if m is not None:
                return m
        return None

    SignatureModel = _get_signature_model()
    if SignatureModel is None:
        html = """
        <div class='alert alert-warning'>
          目前尚無簽名紀錄。
        </div>
        """
        return page("簽名管理", html)

    
    q = SignatureModel.query

    
    cls_unit  = getattr(current_user, "unit", None)
    cls_grade = getattr(current_user, "grade", None)
    cls_class = getattr(current_user, "class_no", None)

    if current_user.role == "teacher" and cls_unit and cls_grade and cls_class:
        
        try:
            q = q.filter_by(unit=cls_unit, grade=str(cls_grade), class_no=str(cls_class))
        except Exception:
            
            pass

    
    order_col = None
    for c in ("created_at", "signed_at", "timestamp"):
        if hasattr(SignatureModel, c):
            order_col = getattr(SignatureModel, c)
            break

    if order_col is not None:
        q = q.order_by(order_col.desc())
    else:
        
        q = q.order_by(SignatureModel.id.desc())

    records = q.limit(300).all()  

    
    def _stu_name(rec):
        return (
            getattr(rec, "student_display", None)
            or getattr(rec, "student_name", None)
            or getattr(rec, "student_username", None)
            or getattr(rec, "owner_name", None)
            or getattr(rec, "username", None)
            or "-"
        )

    def _stu_class(rec):
        g = getattr(rec, "grade", None)
        c = getattr(rec, "class_no", None)
        if g is not None and c is not None:
            return f"{g}年{c}班"
        return getattr(rec, "class_text", "") or "—"

    def _time_text(rec):
        ts = (
            getattr(rec, "created_at", None)
            or getattr(rec, "signed_at", None)
            or getattr(rec, "timestamp", None)
        )
        try:
            return ts.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "—"

    def _note(rec):
        return (
            getattr(rec, "note", None)
            or getattr(rec, "remark", None)
            or getattr(rec, "memo", None)
            or ""
        )

    def _created_by(rec):
        val = getattr(rec, "created_by", None) or getattr(rec, "teacher_name", None)
        return display_name_of(val) if val else "—"

    def _file_img(rec):
        
        fn = (
            getattr(rec, "file_path", None)
            or getattr(rec, "filename", None)
            or getattr(rec, "image", None)
            or getattr(rec, "signature_image", None)
        )
        if not fn:
            return "<span class='text-muted small'>尚無圖片</span>"

        
        return img_html(fn, maxw=220)

    
    rows_html = ""
    for idx, rec in enumerate(records, start=1):
        rows_html += (
            "<tr>"
            f"<td class='text-nowrap'>{idx}</td>"
            f"<td class='text-nowrap'>{_stu_name(rec)}</td>"
            f"<td class='text-nowrap'>{_stu_class(rec)}</td>"
            f"<td class='text-nowrap'>{_time_text(rec)}</td>"
            f"<td>{_note(rec)}</td>"
            f"<td class='text-center'>{_file_img(rec)}</td>"
            f"<td class='text-nowrap text-end'>"
            f"<span class='small text-muted me-2'>{_created_by(rec)}</span>"
            f"<a class='btn btn-sm btn-outline-danger' "
            f"href='/signatures/delete/{rec.id}' "
            "onclick='return confirm(\"確定要刪除此簽名紀錄？此動作無法復原。\");'>刪除</a>"
            "</td>"
            "</tr>"
        )

    if not rows_html:
        rows_html = (
            "<tr><td colspan='7' class='text-center text-muted'>目前尚無簽名紀錄。</td></tr>"
        )

    
    head = (
        "<div class='d-flex justify-content-between align-items-center mb-3'>"
        "<div>"
        "<h5 class='mb-1'>簽名管理</h5>"
        "</div>"
        "<div class='btn-group'>"
        "<a class='btn btn-sm btn-outline-secondary' href='/teacher'>返回老師工作區</a>"
        "</div>"
        "</div>"
    )

    table_html = (
        "<div class='card border-0 shadow-sm'>"
        "<div class='card-body'>"
        "<div class='table-responsive'>"
        "<table class='table table-sm align-middle'>"
        "<thead>"
        "<tr>"
        "<th>#</th>"
        "<th>學生</th>"
        "<th>班級</th>"
        "<th>時間</th>"
        "<th>備註 / 說明</th>"
        "<th>簽名圖片</th>"
        "<th class='text-end'>建立者 / 操作</th>"
        "</tr>"
        "</thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
        "</div>"
        "<div class='small text-muted mt-2'>"
        "刪除後無法復原，請審慎操作。"
        "</div>"
        "</div>"
        "</div>"
    )

    return page("簽名管理", head + table_html)

@app.route("/signatures/delete/<int:sig_id>")
@roles_required("teacher", "leader", "admin")
def signature_delete(sig_id):
    """
    刪除單筆簽名紀錄（連同檔案一起嘗試刪除）
    """
    import os

    
    def _get_signature_model():
        for name in ("SignatureRecord", "ParentSignature", "Signature", "SignRecord"):
            m = globals().get(name)
            if m is not None:
                return m
        return None

    SignatureModel = _get_signature_model()
    if SignatureModel is None:
        return toast_redirect("signature_manage", "目前沒有可刪除的簽名紀錄。", "warning")

    rec = SignatureModel.query.get_or_404(sig_id)

    
    cls_unit  = getattr(current_user, "unit", None)
    cls_grade = getattr(current_user, "grade", None)
    cls_class = getattr(current_user, "class_no", None)

    
    if current_user.role == "teacher" and all(
        hasattr(rec, a) for a in ("unit", "grade", "class_no")
    ):
        if not (
            rec.unit == cls_unit
            and str(rec.grade) == str(cls_grade)
            and str(rec.class_no) == str(cls_class)
        ):
            return toast_redirect("signature_manage", "您無權刪除此班級的簽名紀錄。", "warning")

    
    fn = (
        getattr(rec, "file_path", None)
        or getattr(rec, "filename", None)
        or getattr(rec, "image", None)
        or getattr(rec, "signature_image", None)
    )
    if fn:
        try:
            
            base = app.config.get("UPLOAD_FOLDER") or UPLOAD_DIR
            fpath = os.path.join(base, _safe_upload_name(fn))
            if os.path.exists(fpath):
                os.remove(fpath)
        except Exception:
            
            pass

    
    db.session.delete(rec)
    db.session.commit()

    return toast_redirect("signature_manage", "已刪除簽名紀錄。", "success")


@app.route("/teacher_assignments", methods=["GET","POST"])
@login_required
def teacher_assignments_page():
    if is_staff_admin():
        teachers = User.query.filter_by(role="teacher").order_by(User.username.asc()).all()
        target_username = request.values.get("teacher") or (teachers[0].username if teachers else "")
    else:
        teachers = []
        target_username = current_user.username

    if not target_username:
        return page("授課指派管理", "<div class='alert alert-warning'>目前沒有老師帳號可供設定。</div>")

    msg = ""
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if not is_staff_admin() and target_username != current_user.username:
            return redirect(url_for("teacher_assignments_page"))

        if action == "add":
            s = (request.form.get("subject") or "").strip()
            g = (request.form.get("grade") or "").strip()
            c = (request.form.get("class_no") or "").strip()
            if not (s and g and c):
                msg = "請完整選擇科目 / 年級 / 班級。"
            elif s not in CATEGORY_OPTIONS:
                msg = "科目不在允許清單中。"
            else:
                try:
                    db.session.execute(text("""
                        INSERT INTO teaching_assignment (teacher_username, subject, grade, class_no, created_at)
                        VALUES (:t, :s, :g, :c, :dt)
                    """), {"t": target_username, "s": s, "g": g, "c": c, "dt": datetime.now()})
                    db.session.commit()
                    return redirect(url_for("teacher_assignments_page", teacher=target_username))
                except Exception as e:
                    db.session.rollback()
                    msg = "這筆指派已存在。" if "uq_teacher_subject_class" in str(e) else "新增失敗，請稍後再試。"

        elif action == "del":
            tid = request.form.get("tid")
            try:
                db.session.execute(text("DELETE FROM teaching_assignment WHERE id=:id"), {"id": tid})
                db.session.commit()
                return redirect(url_for("teacher_assignments_page", teacher=target_username))
            except Exception:
                db.session.rollback()
                msg = "刪除失敗。"

    
    if "teacher_assignments_full" in globals():
        assigns = teacher_assignments_full(target_username)
    else:
        try:
            rows = db.session.execute(text("""
                SELECT id, subject, grade, class_no
                  FROM teaching_assignment
                 WHERE teacher_username = :u
                 ORDER BY grade ASC, class_no ASC, subject ASC
            """), {"u": target_username}).mappings().all()
            assigns = [(r["id"], r["subject"], r["grade"], r["class_no"]) for r in rows]
        except Exception:
            assigns = []

    target_user = User.query.filter_by(username=target_username).first()
    target_display = (target_user.display_name if target_user else None) or target_username
    is_hm = str(getattr(target_user, "is_homeroom", 0) or 0) == "1"
    hm_g  = (getattr(target_user, "grade", "") or "").strip()
    hm_c  = (getattr(target_user, "class_no", "") or "").strip()
    hm_badge = f"<span class='badge bg-primary ms-2'>班導 {hm_g}年{hm_c}班</span>" if (is_hm and hm_g and hm_c) else ""

    subject_opts = "".join(f"<option value='{s}'>{s}</option>" for s in CATEGORY_OPTIONS)
    grade_opts   = opt(GRADE_OPTIONS)
    class_opts   = opt(CLASS_OPTIONS)

    teacher_select = ""
    if is_staff_admin():
        t_opts = "".join(
            f"<option value='{t.username}' {'selected' if t.username==target_username else ''}>"
            f"{t.display_name or t.username}</option>" for t in teachers
        )
        teacher_select = f"""
        <form method="get" class="mb-3">
          <div class="row g-2 align-items-end">
            <div class="col-md-6">
              <label class="form-label">選擇老師</label>
              <select name="teacher" class="form-select" onchange="this.form.submit()">
                {t_opts}
              </select>
            </div>
          </div>
        </form>
        """

    homeroom_info = (
        f"<div class='alert alert-info py-2'>班導：<b>{hm_g}年{hm_c}班</b></div>"
        if (is_hm and hm_g and hm_c) else ""
    )

    assigns_html = (
        homeroom_info +
        "<ul class='list-group'>" + "".join(
            f"<li class='list-group-item d-flex justify-content-between align-items-center'>"
            f"<div><b>{g}年{c}班</b>／{s}</div>"
            f"<form method='post' class='m-0'>"
            f"<input type='hidden' name='action' value='del'>"
            f"<input type='hidden' name='tid' value='{tid}'>"
            f"<input type='hidden' name='teacher' value='{target_username}'>"
            f"<button class='btn btn-sm btn-outline-danger'>刪除</button>"
            f"</form></li>"
            for (tid, s, g, c) in assigns
        ) + "</ul>"
    ) if assigns or homeroom_info else "<div class='alert alert-info'>尚無指派。</div>"

    content = f"""
    <div class="d-flex justify-content-between align-items-center mb-2">
      <h5 class="m-0">授課指派管理</h5>
      <a class="btn btn-sm btn-outline-secondary" href="{url_for('teacher')}">返回老師頁</a>
    </div>

    {teacher_select}

    <div class="row g-4">
      <div class="col-md-5">
        <div class="card p-3">
          <h6 class="mb-3">新增指派給：<span class="text-primary">{target_display}</span>{hm_badge}</h6>
          <form method="post" class="row g-2">
            <input type="hidden" name="action" value="add">
            <input type="hidden" name="teacher" value="{target_username}">
            <div class="col-12">
              <label class="form-label">科目</label>
              <select name="subject" class="form-select" required>
                <option value="">請選擇</option>{subject_opts}
              </select>
            </div>
            <div class="col-6">
              <label class="form-label">年級</label>
              <select name="grade" class="form-select" required>
                <option value="">請選擇</option>{grade_opts}
              </select>
            </div>
            <div class="col-6">
              <label class="form-label">班級</label>
              <select name="class_no" class="form-select" required>
                <option value="">請選擇</option>{class_opts}
              </select>
            </div>
            <div class="col-12">
              <button class="btn btn-success">新增</button>
            </div>
          </form>
          {("<div class='alert alert-warning mt-3'>" + msg + "</div>") if msg else ""}
        </div>
      </div>

      <div class="col-md-7">
        <div class="card p-3">
          <h6 class="mb-2">已指派清單</h6>
          {assigns_html}
        </div>
      </div>
    </div>
    """
    return page("授課指派管理", content)

@app.route("/homework")
@login_required
def homework():
    date_str = (request.args.get("date") or "").strip()
    dsel = parse_date(date_str) or today()
    if getattr(current_user, "role", "") == "student":
        return page("學生首頁", render_student_home(dsel))
    return page("聯絡簿", render_contactbook_view(dsel))


def render_student_home(dsel: date_cls) -> str:
    unit, grade, class_no = current_user.unit, current_user.grade, current_user.class_no
    today_d = today()
    today_iso = today_d.isoformat()
    dsel_iso = dsel.isoformat()
    is_today = dsel == today_d
    student_name = current_user.display_name or current_user.username
    class_meta = f"{unit or ''} · {grade or ''}年{class_no or ''}班"

    def safe_url_for(endpoint, **values):
        try:
            return url_for(endpoint, **values)
        except Exception:
            return "#"

    base = Task.query.filter(Task.unit == unit).filter(
        (Task.is_school_wide == 1)
        | ((Task.grade == grade) & (Task.class_no == class_no))
    )
    ann_window = (
        base.filter(
            Task.task_type == "announce",
            ((Task.start_date == None) | (Task.start_date <= dsel)),
            ((Task.end_date == None) | (Task.end_date >= dsel)),
        )
        .order_by(Task.id.asc())
        .all()
    )
    hw_window = (
        base.filter(
            ((Task.task_type == "homework") | (Task.task_type == None)),
            ((Task.start_date == None) | (Task.start_date <= dsel)),
            ((Task.end_date == None) | (Task.end_date >= dsel)),
        )
        .order_by(Task.id.asc())
        .all()
    )
    homework_items = _filter_homework_items(hw_window)

    prompt = (
        DiaryPrompt.query.filter_by(unit=unit, grade=grade, class_no=class_no, date=dsel)
        .order_by(DiaryPrompt.id.desc())
        .first()
    )
    diary_sub = (
        DiarySubmission.query.filter_by(prompt_id=prompt.id, student_name=current_user.username).first()
        if prompt
        else None
    )
    diary_locked = bool(prompt and current_app.config.get("LOCK_PAST_DATES", True) and prompt.date and today() > prompt.date)
    if prompt and diary_sub:
        diary_state = "已繳交"
        diary_metric = "已完成"
        diary_badge = "<span class='badge bg-success'>已繳交</span>"
        diary_action = f"<a class='btn student-soft-btn student-soft-btn--green' href='{safe_url_for('diary_write', pid=prompt.id)}'>查看日記</a>"
    elif prompt and diary_locked:
        diary_state = "已截止"
        diary_metric = "已截止"
        diary_badge = "<span class='badge bg-secondary'>已截止</span>"
        diary_action = "<span class='btn student-soft-btn disabled'>已截止</span>"
    elif prompt:
        diary_state = "待完成"
        diary_metric = "未繳交"
        diary_badge = "<span class='badge bg-warning text-dark'>未繳交</span>"
        diary_action = f"<a class='btn student-soft-btn student-soft-btn--green' href='{safe_url_for('diary_write', pid=prompt.id)}'>寫日記</a>"
    else:
        diary_state = "今日無題"
        diary_metric = "無日記"
        diary_badge = "<span class='badge bg-light text-muted border'>今日無題</span>"
        diary_action = ""

    ws, we = _week_range(today_d)
    week_pts, _week_sdg = _user_week_points(current_user.id, ws, we)
    streak = _streak_weeks(current_user.id, today_d)
    q_read = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(Task.task_type == "mission")
    q_read = ct_user_filter(q_read, current_user.id)
    all_read_records = q_read.all()
    m_first, m_last = _month_range(today_d)
    total_pts = 0
    month_pts = 0
    for ct in all_read_records:
        if _ct_status(ct) == "approved":
            score = _safe_int(_ct_load(ct).get("score_total", 0), 0)
            total_pts += score
            try:
                ct_date = _ct_get_timestamp(ct).date()
            except Exception:
                ct_date = None
            if ct_date and m_first <= ct_date <= m_last:
                month_pts += score
    level = _level_of(total_pts)

    def goal_int(v, default):
        return int(v) if isinstance(v, (int, float)) else default

    def pct(cur, goal):
        if not goal or goal <= 0:
            return 0
        return max(0, min(100, round(cur * 100 / goal)))

    week_goal = goal_int(globals().get("READING_WEEK_GOAL", 30), 30)
    month_goal = goal_int(globals().get("READING_MONTH_GOAL", 120), 120)
    term_goal = goal_int(globals().get("READING_TERM_GOAL", 300), 300)
    term_pts = total_pts
    week_pct = pct(week_pts, week_goal)
    month_pct = pct(month_pts, month_goal)
    term_pct = pct(term_pts, term_goal)
    week_label = f"{week_pts} / {week_goal} 分" if week_goal > 0 else f"{week_pts} 分"
    month_label = f"{month_pts} / {month_goal} 分" if month_goal > 0 else f"{month_pts} 分"
    term_label = f"{term_pts} / {term_goal} 分" if term_goal > 0 else f"{term_pts} 分"

    latest_read_records = sorted(all_read_records, key=lambda ct: _ct_get_timestamp(ct), reverse=True)
    sdg_scores = {code: 0 for code, _ in SDG_OPTIONS}
    sdg_name_map = {code: name for code, name in SDG_OPTIONS}
    approved_record_count = 0
    coread_done = 0
    last_coread_ts = None

    for ct in all_read_records:
        if _ct_status(ct) != "approved":
            continue
        approved_record_count += 1
        payload = _ct_load(ct)
        score = _safe_int(payload.get("score_total", 0), 0)
        ts = _ct_get_timestamp(ct)
        ct_date = ts.date() if ts else None

        if ct_date and m_first <= ct_date <= m_last:
            raw_codes = payload.get("sdg_codes")
            if isinstance(raw_codes, (list, tuple)):
                codes = raw_codes
            elif raw_codes:
                codes = [raw_codes]
            elif payload.get("sdg_code"):
                codes = [payload.get("sdg_code")]
            else:
                codes = []
            used_codes = set()
            for raw_code in codes:
                code = _safe_int(raw_code, 0)
                if code not in sdg_scores or code in used_codes:
                    continue
                used_codes.add(code)
                sdg_scores[code] += score

        if reading_parent_coread_done(payload):
            coread_done += 1
            if ts and ((last_coread_ts is None) or ts > last_coread_ts):
                last_coread_ts = ts

    classmates = User.query.filter_by(
        role="student",
        unit=unit,
        grade=grade,
        class_no=class_no,
    ).all()
    class_rank_rows = []
    weekly_class_totals = []
    for student in classmates:
        q_student = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(Task.task_type == "mission")
        q_student = ct_user_filter(q_student, student.id)
        student_total = 0
        for ct in q_student.all():
            if _ct_status(ct) == "approved":
                student_total += _safe_int(_ct_load(ct).get("score_total", 0), 0)
        class_rank_rows.append((student_total, student))
        student_week_pts, _student_week_sdg = _user_week_points(student.id, ws, we)
        weekly_class_totals.append(student_week_pts)

    class_rank_rows.sort(reverse=True, key=lambda item: item[0])
    class_size = len(class_rank_rows)
    self_rank = None
    for idx, (_student_total, student) in enumerate(class_rank_rows, start=1):
        if student.id == current_user.id:
            self_rank = idx
            break
    class_week_avg = round(sum(weekly_class_totals) / len(weekly_class_totals), 1) if weekly_class_totals else 0
    rank_label = f"第 {self_rank} / {class_size} 名" if self_rank else "尚未排序"
    class_rank_hint = f"全班本週平均 {class_week_avg} 分" if class_size else "目前尚無班級閱讀資料"

    sdg_values = list(sdg_scores.values())
    nonzero_sdg_values = [v for v in sdg_values if v > 0]
    if nonzero_sdg_values:
        balance_index = round(
            100
            * (min(nonzero_sdg_values) / max(nonzero_sdg_values))
            * (len(nonzero_sdg_values) / (len(SDG_OPTIONS) or 17)),
            1,
        )
    else:
        balance_index = 0

    sdg_pairs = [(code, sdg_scores.get(code, 0)) for code, _ in SDG_OPTIONS]
    active_sdg_pairs = [(code, score) for code, score in sdg_pairs if score > 0]
    if active_sdg_pairs:
        top_sdg_code, top_sdg_score = max(active_sdg_pairs, key=lambda item: item[1])
        zero_sdg_codes = [code for code, score in sdg_pairs if score == 0]
        recommend_sdg_code = zero_sdg_codes[0] if zero_sdg_codes else min(sdg_pairs, key=lambda item: item[1])[0]
        top_sdg_label = f"{top_sdg_code:02d} {sdg_name_map[top_sdg_code]}"
        recommend_sdg_label = f"{recommend_sdg_code:02d} {sdg_name_map[recommend_sdg_code]}"
        sdg_focus_hint = f"本月最高 {top_sdg_score} 分，可以再補強 {recommend_sdg_label}。"
    else:
        top_sdg_label = "尚未累積"
        recommend_sdg_label = "先從喜歡的主題開始"
        sdg_focus_hint = "完成一次閱讀認證後，這裡會出現你的 SDG 閱讀分布。"

    coread_pct = pct(coread_done, approved_record_count)
    coread_label = f"{coread_done} / {approved_record_count} 筆" if approved_record_count else "尚無紀錄"
    last_coread_label = last_coread_ts.strftime("%Y-%m-%d") if last_coread_ts else "尚未有親子共讀"
    balance_hint = "分布逐漸平均" if balance_index >= 40 else "可以多嘗試不同 SDG 主題"

    max_sdg_score = max(sdg_values) if any(sdg_values) else 0
    if active_sdg_pairs:
        sdg_bars_html = "".join(
            "<div class='student-sdg-bar'>"
            f"<div class='student-sdg-name'>{escape(f'{code:02d} {sdg_name_map[code]}')}</div>"
            "<div class='student-sdg-track'>"
            f"<div class='student-sdg-fill' style='width:{max(8, pct(score, max_sdg_score))}%;'></div>"
            "</div>"
            f"<div class='student-sdg-score'>{score} 分</div>"
            "</div>"
            for code, score in sorted(active_sdg_pairs, key=lambda item: item[1], reverse=True)[:6]
        )
    else:
        sdg_bars_html = "<div class='student-empty'>本月尚未有通過的閱讀認證。</div>"
    sdg_chart_html = _reading_sdg_chart_html(
        sdg_scores,
        sdg_name_map,
        "本月尚未有通過的閱讀認證，完成後會產生長條圖與圓餅圖。",
    )

    def reading_status_badge(status: str) -> str:
        if status == "approved":
            return "<span class='badge bg-success'>已通過</span>"
        if status == "rejected":
            return "<span class='badge bg-danger'>已退回</span>"
        return "<span class='badge bg-warning text-dark'>待審核</span>"

    latest_record_cards = []
    for ct in latest_read_records[:5]:
        payload = _ct_load(ct)
        status = _ct_status(ct)
        ts = _ct_get_timestamp(ct)
        date_text = ts.strftime("%Y-%m-%d") if ts else "未記錄日期"
        book_title = (payload.get("book_title") or getattr(getattr(ct, "task", None), "title", "") or "閱讀紀錄").strip()
        if status == "approved":
            score_text = f"{_safe_int(payload.get('score_total', 0), 0)} 分"
        elif status == "rejected":
            score_text = "未計分"
        else:
            score_text = "審核中"
        raw_codes = payload.get("sdg_codes")
        if isinstance(raw_codes, (list, tuple)):
            pass
        elif raw_codes:
            raw_codes = [raw_codes]
        elif payload.get("sdg_code"):
            raw_codes = [payload.get("sdg_code")]
        else:
            raw_codes = []
        code_labels = []
        for raw_code in raw_codes:
            code = _safe_int(raw_code, 0)
            if code in sdg_name_map:
                code_labels.append(f"{code:02d}")
        sdg_text = "、".join(code_labels) or "未分類"
        coread_tag = "<span class='student-history-tag'>親子共讀</span>" if reading_parent_coread_done(payload) else ""
        latest_record_cards.append(
            "<article class='student-history-item'>"
            "<div class='student-history-main'>"
            f"<div class='student-history-title'>《{escape(book_title)}》</div>"
            f"<div class='student-history-meta'>{date_text} · SDG {escape(sdg_text)} {coread_tag}</div>"
            "</div>"
            "<div class='student-history-side'>"
            f"{reading_status_badge(status)}"
            f"<div class='student-history-score'>{score_text}</div>"
            "</div>"
            "</article>"
        )
    latest_records_html = "".join(latest_record_cards) or "<div class='student-empty'>還沒有閱讀紀錄，完成一次閱讀認證後會顯示在這裡。</div>"

    read_task = _ensure_reading_area_task_for_student(current_user)
    read_link = safe_url_for("reading_submit", task_id=read_task.id)
    assigned_tasks = _assigned_reading_tasks_for_student(current_user)
    assigned_due = 0
    assigned_reviewing = 0
    assigned_cards = []
    for task in assigned_tasks[:4]:
        latest = _latest_submission_for_task(task.id, current_user.username)
        status_html = "<span class='badge bg-light text-muted border'>未提交</span>"
        action_label = "開始任務"
        action_class = "student-soft-btn--green"
        if latest:
            st = _ct_status(latest)
            if st == "approved":
                status_html = "<span class='badge bg-success'>已通過</span>"
                action_label = "查看紀錄"
                action_class = ""
            elif st == "pending":
                status_html = "<span class='badge bg-warning text-dark'>待審核</span>"
                action_label = "查看提交"
                action_class = ""
                assigned_reviewing += 1
            else:
                status_html = "<span class='badge bg-danger'>需重交</span>"
                action_label = "重新提交"
                action_class = "student-soft-btn--green"
                assigned_due += 1
        else:
            assigned_due += 1

        meta = task_meta(task)
        due_txt = escape(str(task.end_date or "不限"))
        parent_badge = "<span class='badge bg-info text-dark'>親子共讀</span>" if meta.get(META_PARENT) == "1" else ""
        action_html = (
            "<span class='btn student-soft-btn disabled'>已截止</span>"
            if task.end_date and today_d > task.end_date and not latest
            else f"<a class='btn student-soft-btn {action_class}' href='{safe_url_for('reading_submit', task_id=task.id)}'>{action_label}</a>"
        )
        assigned_cards.append(
            "<article class='student-task-card'>"
            "<div class='student-task-top'>"
            f"<div><div class='student-task-title'>{escape(task.title)}</div>"
            f"<div class='student-muted'>截止 {due_txt} · {_sdg_label(meta.get(META_SDG_CODE))}</div></div>"
            f"<div class='student-badge-stack'>{status_html}{parent_badge}</div>"
            "</div>"
            f"<div class='student-task-desc'>{_desc_clean_html(task_body(task)) or '依老師指定內容完成閱讀任務。'}</div>"
            f"<div class='text-end mt-3'>{action_html}</div>"
            "</article>"
        )

    meds = []
    try:
        meds = (
            MedicationRecord.query.filter(
                MedicationRecord.date == dsel,
                MedicationRecord.student_name == current_user.username,
            )
            .order_by(MedicationRecord.id.desc())
            .limit(4)
            .all()
        )
    except Exception:
        meds = []

    notes = []
    if "CommNote" in globals():
        try:
            raw_notes = (
                CommNote.query.filter(CommNote.unit == unit, CommNote.date == dsel)
                .order_by(CommNote.id.desc())
                .all()
            )
            for n in raw_notes:
                stu_un = getattr(n, "student_username", None)
                visible = getattr(n, "visible_to_student", 1) == 1
                if not visible:
                    continue
                if stu_un == current_user.username:
                    notes.append(n)
                elif (
                    not stu_un
                    and getattr(n, "author_role", "") == "teacher"
                    and str(getattr(n, "grade", "") or "") == str(grade or "")
                    and str(getattr(n, "class_no", "") or "") == str(class_no or "")
                ):
                    notes.append(n)
        except Exception:
            notes = []

    def hero_metric(label, value, sub=""):
        extra = f"<div class='student-metric-sub'>{sub}</div>" if sub else ""
        return (
            "<div class='student-metric'>"
            f"<div class='student-metric-value'>{value}</div>"
            f"<div class='student-metric-label'>{label}</div>"
            f"{extra}"
            "</div>"
        )

    def announce_html():
        cards = []
        for t in ann_window[:4]:
            thumb = img_html(task_img_name(t), maxw=240)
            cards.append(
                "<div class='student-mini-item'>"
                f"<div class='fw-bold'>{escape(t.title)}</div>"
                f"<div class='student-muted'>{escape(t.category or '公告')} · {escape(display_name_of(t.created_by) or '')}</div>"
                f"<div class='mt-1'>{_desc_clean_html(t.description or '')}</div>"
                f"{thumb}"
                "</div>"
            )
        return "".join(cards)

    if prompt:
        diary_instruction = (
            f"<div class='student-muted mt-2'>{_user_text_html(prompt.instruction)}</div>"
            if getattr(prompt, "instruction", None)
            else ""
        )
        diary_prompt_img = img_html(diary_prompt_img_name(prompt), maxw=260)
        diary_body = (
            f"<div class='student-diary-title'>{escape(prompt.title or '日記題目')}</div>"
            f"{diary_instruction}"
            f"{diary_prompt_img}"
        )
    else:
        diary_body = "<div class='student-empty'>今天老師尚未發布日記題目。</div>"

    diary_action_html = f"<div class='student-diary-actions'>{diary_action}</div>" if diary_action else ""

    def student_homework_diary_table():
        preferred = PREFERRED_SUBJECT_ORDER
        base_order = [s for s in preferred if s in CATEGORY_OPTIONS] + [c for c in CATEGORY_OPTIONS if c not in preferred]
        by_cat = {}
        for t in homework_items:
            by_cat.setdefault(t.category or "其他", []).append(t)
        present_order = [c for c in base_order if c in by_cat] + sorted([c for c in by_cat.keys() if c not in base_order])

        def hw_cell(items):
            out = []
            for t in items:
                deadline = f"<small class='text-muted ms-2'>截止：{t.end_date}</small>" if t.end_date else ""
                thumb = img_html(task_img_name(t), maxw=280)
                out.append(
                    "<div class='student-homework-card'>"
                    f"<div class='student-homework-title'>{escape(t.title)}</div>"
                    f"<div class='student-homework-desc'>{_desc_clean_html(t.description or '')}</div>"
                    f"{thumb}"
                    f"<div class='student-homework-meta'>教師：{escape(display_name_of(t.created_by) or '')}{deadline}</div>"
                    "</div>"
                )
            return "".join(out)

        rows = "".join(
            "<article class='student-work-row'>"
            f"<div class='student-work-label'>{cat}</div>"
            f"<div class='student-work-content'>{hw_cell(by_cat.get(cat, []))}</div>"
            "</article>"
            for cat in present_order
        )
        if not rows:
            rows = (
                "<article class='student-work-row student-work-row--empty'>"
                "<div class='student-work-label'>作業</div>"
                "<div class='student-work-content'><div class='student-empty student-empty--soft'>今天沒有作業</div></div>"
                "</article>"
            )
        diary_head = f"<div class='student-badge-stack mb-2'>{diary_badge}</div>" if prompt else ""
        rows += (
            "<article class='student-work-row student-work-row--diary'>"
            "<div class='student-work-label'>日記</div>"
            "<div class='student-work-content'>"
            "<div class='student-diary-inline-card'>"
            f"{diary_head}"
            f"{diary_body}"
            f"{diary_action_html}"
            "</div>"
            "</div>"
            "</article>"
        )
        return (
            "<div class='student-work-board'>"
            f"{rows}"
            "</div>"
        )

    homework_html = student_homework_diary_table()

    meds_html = (
        "".join(
            "<div class='student-mini-item'>"
            f"<div class='fw-bold'>{escape(m.medicine_name)} {_status_badge_map(m.status, MEDICATION_STATUS_OPTIONS)}</div>"
            f"<div class='student-muted'>時間：{escape(m.time_note or '未指定')} · 劑量：{escape(m.dose or '未填')}</div>"
            "</div>"
            for m in meds
        )
    )

    notes_html = (
        "".join(
            "<div class='student-mini-item'>"
            f"<div class='fw-bold'>{escape(n.title)}</div>"
            f"<div class='student-muted'>{escape(display_name_of(n.created_by) or '')}</div>"
            f"{img_html(getattr(n, 'image', None), maxw=240)}"
            "</div>"
            for n in notes[:3]
        )
    )

    today_sections = []
    ann_html = announce_html()
    if ann_html:
        today_sections.append(
            "<div class='student-subsection'>"
            "<div class='student-subtitle'>公告</div>"
            f"{ann_html}"
            "</div>"
        )
    today_sections.append(
        "<div class='student-subsection'>"
        "<div class='student-subtitle'>作業與日記</div>"
        f"{homework_html}"
        "</div>"
    )
    if meds_html:
        today_sections.append(
            "<div class='student-subsection'>"
            "<div class='student-subtitle'>用藥紀錄</div>"
            f"{meds_html}"
            "</div>"
        )
    if notes_html:
        today_sections.append(
            "<div class='student-subsection'>"
            "<div class='student-subtitle'>交流訊息</div>"
            f"{notes_html}"
            "</div>"
        )
    today_sections_html = "".join(today_sections) or "<div class='student-empty student-empty--panel'>今天沒有需要處理的聯絡簿內容。</div>"

    prev_d = (dsel - timedelta(days=1)).isoformat()
    next_d = (dsel + timedelta(days=1)).isoformat()
    date_nav = (
        "<div class='student-date-nav'>"
        f"<a class='btn student-soft-btn' href='{safe_url_for('homework', date=prev_d)}'>前一天</a>"
        "<form method='get' class='student-date-form'>"
        f"<input type='date' name='date' class='form-control' value='{dsel_iso}'>"
        "<button class='btn student-soft-btn student-soft-btn--green'>前往</button>"
        "</form>"
        f"<a class='btn student-soft-btn' href='{safe_url_for('homework', date=today_iso)}'>今日</a>"
        f"<a class='btn student-soft-btn' href='{safe_url_for('homework', date=next_d)}'>下一天</a>"
        "</div>"
    )

    style = """
    <style>
    .student-page{display:flex;flex-direction:column;gap:1.1rem;}
    .student-hero{position:relative;overflow:hidden;border-radius:30px;padding:1.4rem;background:linear-gradient(135deg,#eefcf2 0%,#fff7dd 58%,#eef7ff 100%);border:1px solid rgba(36,92,59,.1);box-shadow:0 24px 60px rgba(31,79,50,.12);}
    .student-hero:before{content:"";position:absolute;right:-5rem;top:-5rem;width:18rem;height:18rem;border-radius:50%;background:rgba(89,167,113,.16);}
    .student-hero-inner{position:relative;display:flex;justify-content:space-between;gap:1.2rem;align-items:flex-start;flex-wrap:wrap;}
    .student-eyebrow,.student-section-kicker{font-size:.78rem;font-weight:900;letter-spacing:.1em;text-transform:uppercase;color:#4f8f61;margin-bottom:.25rem;}
    .student-hero h3,.student-panel h4{margin:0;color:#162318;font-weight:900;letter-spacing:-.02em;}
    .student-muted{color:#647067;font-size:.94rem;}
    .student-hero-actions{display:flex;gap:.55rem;align-items:center;flex-wrap:wrap;justify-content:flex-end;}
    .student-soft-btn{border-radius:999px!important;border:1px solid rgba(35,92,59,.18)!important;background:#fff!important;color:#244c32!important;font-weight:850!important;padding:.5rem .95rem!important;box-shadow:0 10px 24px rgba(24,76,46,.08);}
    .student-soft-btn:hover{background:#f3fbf5!important;color:#16351f!important;transform:translateY(-1px);}
    .student-soft-btn--green{background:#34784a!important;color:#fff!important;border-color:#34784a!important;}
    .student-soft-btn--green:hover{background:#28613b!important;color:#fff!important;}
    .student-metric-grid{position:relative;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;margin-top:1.1rem;}
    .student-metric{background:rgba(255,255,255,.86);border:1px solid rgba(35,92,59,.1);border-radius:20px;padding:.95rem 1rem;box-shadow:0 14px 32px rgba(24,76,46,.08);}
    .student-metric-value{font-size:1.55rem;line-height:1;font-weight:900;color:#183d28;margin-bottom:.35rem;}
    .student-metric-label{font-size:.88rem;color:#2f4738;font-weight:850;}
    .student-metric-sub{font-size:.78rem;color:#7b887f;margin-top:.12rem;}
	    .student-main-grid{display:grid;grid-template-columns:1fr;gap:1rem;align-items:start;}
    .student-panel{background:rgba(255,255,255,.97);border:1px solid rgba(19,56,35,.08);border-radius:28px;box-shadow:0 22px 54px rgba(24,76,46,.1);overflow:hidden;}
    .student-panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;padding:1.15rem 1.25rem;border-bottom:1px solid rgba(19,56,35,.08);background:linear-gradient(180deg,#ffffff 0%,#fbfdf8 100%);}
    .student-panel-body{padding:1.1rem 1.25rem;}
    .student-subsection{border-radius:22px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;margin-bottom:.9rem;}
    .student-subsection:last-child{margin-bottom:0;}
    .student-subsection-head{display:flex;justify-content:space-between;align-items:flex-start;gap:.75rem;flex-wrap:wrap;margin-bottom:.65rem;}
    .student-subtitle{font-weight:900;color:#183d28;margin-bottom:.65rem;}
    .student-subsection-head .student-subtitle{margin-bottom:0;}
    .student-empty{color:#718078;font-size:.94rem;padding:.35rem 0;}
    .student-empty--panel{border-radius:20px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .student-empty--soft{border-radius:16px;background:#f8fbf6;border:1px dashed rgba(35,92,59,.16);padding:1rem;}
    .student-page table{margin-bottom:0;background:#fff;border-radius:16px;overflow:hidden;}
    .student-page table th{color:#244c32;background:#f4faf5;}
    .student-homework-diary-table td{background:#fff;}
    .student-homework-item{margin-bottom:.9rem;}
    .student-homework-item:last-child{margin-bottom:0;}
    .student-work-board{display:flex;flex-direction:column;gap:.8rem;}
    .student-work-row{display:grid;grid-template-columns:7.5rem minmax(0,1fr);gap:.85rem;align-items:stretch;border-radius:22px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:.85rem;box-shadow:0 10px 24px rgba(24,76,46,.05);}
    .student-work-label{display:flex;align-items:center;justify-content:center;min-height:3.25rem;border-radius:16px;background:#eef8f0;color:#244c32;font-weight:950;font-size:1.08rem;letter-spacing:.04em;text-align:center;}
    .student-work-content{min-width:0;display:flex;flex-direction:column;gap:.75rem;}
    .student-work-row--diary{background:linear-gradient(135deg,#ffffff 0%,#fbfff8 100%);}
    .student-homework-card{border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .student-homework-title{font-size:1.1rem;font-weight:950;color:#17231b;line-height:1.35;}
    .student-homework-desc{color:#46524a;font-size:.98rem;line-height:1.7;margin-top:.35rem;}
    .student-homework-meta{color:#647067;font-size:.88rem;font-weight:800;margin-top:.55rem;}
    .student-diary-inline-card{border-radius:18px;background:#fbfdf8;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .student-diary-row th{background:#eef8f0!important;}
    .student-diary-cell-head{display:flex;justify-content:space-between;align-items:flex-start;gap:.75rem;flex-wrap:wrap;margin-bottom:.45rem;}
    .student-mini-item{background:#fff;border:1px solid rgba(19,56,35,.08);border-radius:16px;padding:.85rem;margin-bottom:.6rem;}
    .student-mini-item:last-child{margin-bottom:0;}
    .student-diary-title{font-weight:900;font-size:1.1rem;color:#17231b;}
    .student-diary-card,.student-reading-card{border-radius:22px;border:1px solid rgba(19,56,35,.08);background:linear-gradient(180deg,#fff 0%,#fbfff8 100%);padding:1rem;box-shadow:0 14px 30px rgba(24,76,46,.07);}
    .student-diary-card--embedded{box-shadow:none;background:#fff;margin-top:.35rem;}
    .student-diary-actions{margin-top:.9rem;text-align:right;}
    .student-diary-top,.student-task-top{display:flex;justify-content:space-between;gap:.8rem;align-items:flex-start;flex-wrap:wrap;margin-bottom:.85rem;}
    .student-badge-stack{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:flex-end;}
    .student-task-list{display:flex;flex-direction:column;gap:.8rem;margin-top:.9rem;}
    .student-task-card{border-radius:20px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .student-task-title{font-weight:900;color:#17231b;}
    .student-task-desc{color:#46524a;font-size:.93rem;line-height:1.65;margin-top:.4rem;}
    .student-reading-dashboard{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem;margin-bottom:1rem;}
    .student-reading-stat{border-radius:18px;background:#f6faf7;border:1px solid rgba(35,92,59,.08);padding:1rem;}
    .student-reading-stat-value{font-size:1.45rem;font-weight:900;color:#183d28;line-height:1;}
    .student-reading-stat-label{font-size:.82rem;color:#647067;font-weight:850;margin-top:.35rem;}
    .student-reading-sections{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr);gap:1rem;margin-top:1rem;}
    .student-reading-section{border-radius:22px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:1rem;box-shadow:0 10px 24px rgba(24,76,46,.05);}
    .student-reading-chart-section,.student-reading-history-section{grid-column:1/-1;}
    .student-reading-section-title{font-weight:900;color:#183d28;margin-bottom:.75rem;display:flex;align-items:center;justify-content:space-between;gap:.75rem;flex-wrap:wrap;}
    .student-insight-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem;margin:1rem 0;}
    .student-insight-card{border-radius:20px;background:linear-gradient(180deg,#ffffff 0%,#f6fbf7 100%);border:1px solid rgba(35,92,59,.08);padding:1rem;min-height:8.2rem;}
    .student-insight-value{font-size:1.35rem;font-weight:900;color:#183d28;line-height:1.15;margin:.25rem 0;}
    .student-insight-label{font-size:.82rem;font-weight:900;color:#4f8f61;letter-spacing:.05em;text-transform:uppercase;}
    .student-insight-note{font-size:.85rem;color:#647067;line-height:1.45;}
    .student-sdg-bars{display:flex;flex-direction:column;gap:.7rem;}
    .student-sdg-bar{display:grid;grid-template-columns:8.8rem minmax(0,1fr) 4.2rem;gap:.75rem;align-items:center;}
    .student-sdg-name{font-weight:850;color:#2d4736;font-size:.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .student-sdg-track{height:.72rem;border-radius:999px;background:#e8f2ea;overflow:hidden;}
    .student-sdg-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,#34784a,#8dcf72);}
    .student-sdg-score{font-size:.86rem;font-weight:850;color:#647067;text-align:right;}
    .student-history-list{display:flex;flex-direction:column;gap:.65rem;}
    .student-history-item{display:flex;justify-content:space-between;align-items:center;gap:.9rem;border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:.85rem .95rem;}
    .student-history-main{min-width:0;}
    .student-history-title{font-weight:900;color:#17231b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .student-history-meta{font-size:.86rem;color:#647067;margin-top:.15rem;}
    .student-history-tag{display:inline-flex;margin-left:.3rem;border-radius:999px;background:#e5f4ea;color:#2f7447;font-size:.75rem;font-weight:850;padding:.12rem .45rem;}
    .student-history-side{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:.25rem;flex-shrink:0;}
    .student-history-score{font-size:.86rem;color:#647067;font-weight:850;}
    .student-progress-line{display:grid;grid-template-columns:7rem minmax(0,1fr) 7.5rem;gap:.75rem;align-items:center;margin:.8rem 0;}
    .student-progress-line .progress{height:.72rem;border-radius:999px;background:#e8f2ea;}
    .student-progress-line .progress-bar{border-radius:999px;background:#34784a;}
    .student-progress-name{font-weight:900;color:#244c32;}
    .student-progress-number{font-size:.88rem;color:#647067;text-align:right;font-weight:800;}
    .student-progress-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.9rem;}
    .student-progress-card{border-radius:20px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .student-progress-value{font-size:1.8rem;font-weight:900;color:#183d28;line-height:1;}
    .student-progress-label{font-size:.9rem;color:#647067;font-weight:800;margin-top:.35rem;}
    .student-date-nav{display:flex;gap:.55rem;align-items:center;flex-wrap:wrap;}
    .student-date-form{display:flex;gap:.45rem;align-items:center;}
	    .student-date-form .form-control{border-radius:999px;min-width:10rem;}
	    @media (max-width:1199.98px){
	      .student-metric-grid,.student-reading-dashboard,.student-insight-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
      .student-reading-sections{grid-template-columns:1fr;}
	    }
	    @media (max-width:767.98px){
	      .student-hero,.student-panel{border-radius:22px;}
	      .student-metric-grid,.student-work-row,.student-progress-grid,.student-reading-dashboard,.student-insight-grid{grid-template-columns:1fr;}
      .student-work-label{justify-content:flex-start;min-height:auto;padding:.65rem .8rem;}
      .student-progress-line{grid-template-columns:1fr;gap:.35rem;}
      .student-sdg-bar{grid-template-columns:1fr;gap:.35rem;}
      .student-sdg-score{text-align:left;}
      .student-history-item{align-items:flex-start;flex-direction:column;}
      .student-history-side{text-align:left;align-items:flex-start;}
      .student-progress-number{text-align:left;}
      .student-hero-actions{justify-content:flex-start;}
      .student-panel-head,.student-panel-body{padding:1rem;}
      .student-date-form{width:100%;}
      .student-date-form .form-control{flex:1;min-width:0;}
    }
    </style>
    """

    hero = f"""
    <section class='student-hero'>
      <div class='student-hero-inner'>
        <div>
          <div class='student-eyebrow'>學生總覽</div>
          <h3>你好，{escape(student_name)}</h3>
          <div class='student-muted mt-1'>{escape(class_meta)} · {'今天' if is_today else '查看'} {dsel_iso}</div>
        </div>
        <div class='student-hero-actions'>
          <a class='btn student-soft-btn' href='/profile'>個人檔案</a>
          <a class='btn student-soft-btn' href='/comm'>親師交流</a>
        </div>
      </div>
      <div class='student-metric-grid'>
        {hero_metric("今日作業", len(homework_items), "老師布置項目")}
        {hero_metric("日記", diary_metric, diary_state)}
        {hero_metric("閱讀任務", assigned_due, f"待審核 {assigned_reviewing}")}
        {hero_metric("本週閱讀", week_pts, f"{level} · 連續 {streak} 週")}
      </div>
    </section>
    """

    today_panel = f"""
    <section class='student-panel'>
      <div class='student-panel-head'>
        <div>
          <div class='student-section-kicker'>Today</div>
          <h4>今日作業、公告與日記</h4>
        </div>
        {date_nav}
      </div>
      <div class='student-panel-body'>
        {today_sections_html}
      </div>
    </section>
    """

    reading_panel = f"""
    <section class='student-panel'>
      <div class='student-panel-head'>
        <div>
          <div class='student-section-kicker'>Reading</div>
          <h4>閱讀專區</h4>
        </div>
        <div class='d-flex flex-wrap gap-2'>
          <a class='btn student-soft-btn student-soft-btn--green' href='{read_link}'>閱讀認證</a>
        </div>
      </div>
      <div class='student-panel-body'>
        <div class='student-reading-card'>
          <div class='d-flex justify-content-between align-items-start gap-2 flex-wrap'>
            <div>
              <div class='student-section-kicker mb-1'>Dashboard</div>
              <div class='student-diary-title'>閱讀儀表板與進度</div>
              <div class='student-muted'>閱讀成果、目標進度與老師指定任務集中在這裡。</div>
            </div>
          </div>
          <div class='student-reading-dashboard mt-3'>
            <div class='student-reading-stat'>
              <div class='student-reading-stat-value'>{week_pts}</div>
              <div class='student-reading-stat-label'>本週分數</div>
            </div>
            <div class='student-reading-stat'>
              <div class='student-reading-stat-value'>{streak}</div>
              <div class='student-reading-stat-label'>連續週數</div>
            </div>
            <div class='student-reading-stat'>
              <div class='student-reading-stat-value'>{level}</div>
              <div class='student-reading-stat-label'>目前等級</div>
            </div>
            <div class='student-reading-stat'>
              <div class='student-reading-stat-value'>{total_pts}</div>
              <div class='student-reading-stat-label'>累積分數</div>
            </div>
          </div>
          <div class='student-progress-line'>
            <div class='student-progress-name'>本週</div>
            <div class='progress'><div class='progress-bar' style='width:{week_pct}%;'></div></div>
            <div class='student-progress-number'>{week_label}</div>
          </div>
          <div class='student-progress-line'>
            <div class='student-progress-name'>本月</div>
            <div class='progress'><div class='progress-bar' style='width:{month_pct}%;'></div></div>
            <div class='student-progress-number'>{month_label}</div>
          </div>
          <div class='student-progress-line'>
            <div class='student-progress-name'>本學期</div>
            <div class='progress'><div class='progress-bar' style='width:{term_pct}%;'></div></div>
            <div class='student-progress-number'>{term_label}</div>
          </div>
          <div class='student-insight-grid'>
            <div class='student-insight-card'>
              <div class='student-insight-label'>班級位置</div>
              <div class='student-insight-value'>{rank_label}</div>
              <div class='student-insight-note'>{class_rank_hint}</div>
            </div>
            <div class='student-insight-card'>
              <div class='student-insight-label'>SDG 主題</div>
              <div class='student-insight-value'>{top_sdg_label}</div>
              <div class='student-insight-note'>{sdg_focus_hint}</div>
            </div>
            <div class='student-insight-card'>
              <div class='student-insight-label'>親子共讀</div>
              <div class='student-insight-value'>{coread_label}</div>
              <div class='student-insight-note'>完成率 {coread_pct}% · 最近 {last_coread_label}</div>
            </div>
            <div class='student-insight-card'>
              <div class='student-insight-label'>閱讀均衡</div>
              <div class='student-insight-value'>{balance_index}%</div>
              <div class='student-insight-note'>{balance_hint}</div>
            </div>
          </div>
          <div class='student-reading-sections'>
            <section class='student-reading-section student-reading-chart-section'>
              <div class='student-reading-section-title'>
                <span>本月 SDG 圖表</span>
                <span class='student-muted'>建議：{recommend_sdg_label}</span>
              </div>
              {sdg_chart_html}
            </section>
            <section class='student-reading-section student-reading-history-section'>
              <div class='student-reading-section-title'>
                <span>最近閱讀紀錄</span>
                <span class='student-muted'>顯示最新 5 筆</span>
              </div>
              <div class='student-history-list'>{latest_records_html}</div>
            </section>
          </div>
          <div class='student-reading-section mt-3'>
            <div class='student-reading-section-title'>
              <span>老師指定閱讀任務</span>
              <span class='student-muted'>共 {len(assigned_tasks)} 項，待完成 {assigned_due} 項</span>
            </div>
            <div class='student-task-list'>
              {''.join(assigned_cards) or "<div class='student-empty'>目前沒有老師指定的閱讀任務。</div>"}
            </div>
          </div>
        </div>
      </div>
    </section>
    """

    return (
        style
        + "<div class='student-page'>"
        + hero
        + "<div class='student-main-grid'>"
        + today_panel
        + reading_panel
        + "</div>"
        + "</div>"
    )

def render_contactbook_view(
    dsel: date_cls,
    include_diary: bool = True,
    show_toolbar: bool = True,
) -> str:
    from datetime import timedelta
    from flask import url_for, current_app
    from sqlalchemy import func
    import json, re

    unit, grade, class_no = current_user.unit, current_user.grade, current_user.class_no

    
    SUSTAIN_CATS  = [c for c in globals().get("SUSTAIN_OPTIONS",  ["節能", "節水", "低碳交通", "飲食", "資源回收"]) if str(c) != "其他"]
    ANNOUNCE_CATS = globals().get("ANNOUNCE_OPTIONS", ["行為守則", "活動訊息", "安全提醒", "行政事項", "其他"])

    
    def display_name_of(username: str) -> str:
        try:
            u = User.query.filter_by(username=username).first()  
            return (u.display_name or u.username) if u else (username or "")
        except Exception:
            return username or ""

    def _desc_clean_html(s: str) -> str:
        s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
        return _user_text_html(s)

    def is_sustain_task(t: "Task") -> bool:
        
        return (getattr(t, "task_type", "homework") == "mission") or ((t.category or "其他") in SUSTAIN_CATS)

    
    def task_img_name(t: "Task"):
        
        for key in ("image","attachment_image","image_name","image_file",
                    "cover_image","photo","picture","img","mission_image","homework_image"):
            if hasattr(t, key):
                val = getattr(t, key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        
        if hasattr(t, "images"):
            val = getattr(t, "images")
            if isinstance(val, list) and val:
                fst = val[0]
                if isinstance(fst, str) and fst.strip():
                    return fst.strip()
            if isinstance(val, str) and val.strip():
                parts = [p.strip() for p in val.split(",") if p.strip()]
                if parts:
                    return parts[0]
        
        if hasattr(t, "extra"):
            val = getattr(t, "extra")
            try:
                data = json.loads(val) if isinstance(val, str) else (val or {})
                for key in ["image","cover_image","picture","img"]:
                    if key in (data or {}):
                        cand = data[key]
                        if isinstance(cand, str) and cand.strip():
                            return cand.strip()
                        if isinstance(cand, list) and cand:
                            return str(cand[0]).strip()
            except Exception:
                pass
        return None

    def _img_src(name: str) -> str:
        if not name:
            return ""
        s = name.strip().replace("\\", "/")
        if s.startswith("http://") or s.startswith("https://") or s.startswith("/static/"):
            return s
        s = re.sub(r"^(?:/)?(?:uploads/)+", "", s)  
        return f"/uploads/{s}"

    def img_html(name: str, maxw: int = 220) -> str:
        if not name:
            return ""
        src = escape(_img_src(name))
        width = max(maxw or 0, 280)
        return (
            "<figure class='attachment-preview'>"
            f"<a target='_blank' href='{src}' class='attachment-preview__link' title='點擊放大圖片'>"
            f"<img src='{src}' class='attachment-preview__image' style='max-width:{width}px;' alt='附圖'>"
            "<span class='attachment-preview__hint'>點擊放大</span>"
            "</a>"
            "</figure>"
        )

    def _norm(s: str) -> str:
        s = (s or "").strip()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[，。、「」『』！!？?\.\-,;:~…\(\)\[\]{}《》〈〉]+", "", s)
        return s

    
    def build_toolbar() -> str:
        if not show_toolbar:
            return ""
        if current_user.role == "student":
            code = (str(int(current_user.username) * 13 + 13) if str(current_user.username).isdigit() else "（學號非數字，無法產生）")
            return (
                "<div class='mb-2 text-end'>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/profile'>個人檔案</a>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/comm'>師生交流</a>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/medication'>用藥紀錄</a>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/leave/mine'>我的假單</a>"
                f"<button class='btn btn-sm btn-outline-dark' type='button' onclick=\"navigator.clipboard.writeText('{code}');alert('已複製家長邀請碼');\">家長邀請碼</button>"
                "</div>"
            )
        if current_user.role == "parent":
            return (
                "<div class='mb-2 text-end'>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/profile'>個人檔案</a>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/comm'>親師交流</a>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/medication'>用藥紀錄</a>"
                f"<a class='btn btn-sm btn-outline-primary' href='/parent?date={dsel.isoformat()}#parent-diary-panel'>日記與簽名</a>"
                "</div>"
            )
        if current_user.role == "teacher":
            return (
                "<div class='mb-2 text-end'>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/profile'>個人檔案</a>"
                "<a class='btn btn-sm btn-outline-primary me-2' href='/teacher_diary'>老師｜日記題目</a>"
                "<a class='btn btn-sm btn-outline-dark me-2' href='/comm'>親師交流</a>"
                "<a class='btn btn-sm btn-outline-dark' href='/medication'>用藥紀錄</a>"
                "</div>"
            )
        return ""

    
    base = Task.query.filter(Task.unit == unit)
    if current_user.role in ("student", "parent", "teacher"):
        base = base.filter((Task.is_school_wide == 1) |
                           ((Task.grade == grade) & (Task.class_no == class_no)))

    
    ann_window = (
        base.filter(
            Task.task_type == "announce",
            ((Task.start_date == None) | (Task.start_date <= dsel)),            
            ((Task.end_date == None) | (Task.end_date >= dsel))                 
        ).order_by(Task.id.asc()).all()
    )
    hw_window = (
        base.filter(
            ((Task.task_type == "homework") | (Task.task_type == None)),
            ((Task.start_date == None) | (Task.start_date <= dsel)),            
            ((Task.end_date == None) | (Task.end_date >= dsel))                 
        ).order_by(Task.id.asc()).all()
    )

    toolbar = build_toolbar()

    
    prev_d = (dsel - timedelta(days=1)).isoformat()
    next_d = (dsel + timedelta(days=1)).isoformat()
    nav = (
        "<div class='d-flex justify-content-between align-items-center mb-3 flex-wrap'>"
        f"<div class='me-2 mb-2'><a class='btn btn-sm btn-outline-secondary' href='{url_for('homework', date=prev_d)}'>&laquo; 前一天</a></div>"
        "<form method='get' class='d-flex align-items-center gap-2 mb-2'>"
        f"<input type='date' name='date' class='form-control form-control-sm' value='{dsel}'>"
        "<button class='btn btn-sm btn-primary'>前往</button></form>"
        f"<div class='ms-2 mb-2'><a class='btn btn-sm btn-outline-secondary' href='{url_for('homework', date=next_d)}'>下一天 &raquo;</a></div>"
        "</div>"
    )
    subtitle = f"<div class='small text-muted mb-2'>{unit or ''} · {(str(grade)+'年'+str(class_no)+'班') if current_user.role in ('student','parent','teacher') else '全校'} · {dsel}</div>"

    
    announce_block = ""
    if ann_window:
        
        by_cat = {}
        for t in ann_window:
            cat = t.category or "其他"
            by_cat.setdefault(cat, []).append(t)

        def ann_list(items):
            out = []
            for t in items:
                deadline = f"<small class='text-muted ms-2'>截止：{t.end_date or '—'}</small>"
                thumb = img_html(task_img_name(t))
                out.append(
                    "<div class='mb-3 p-2 border rounded bg-light'>"
                    f"<div class='fw-bold'>{t.title}</div>"
                    f"<div class='small text-muted'>發布：{t.start_date or '—'} · {deadline} · {display_name_of(t.created_by)}</div>"
                    f"<div class='mt-1'>{_desc_clean_html(t.description or '')}</div>"
                    f"{thumb}"
                    "</div>"
                )
            return "".join(out)

        
        ordered_cats = [c for c in ANNOUNCE_CATS if c in by_cat] + [
            c for c in by_cat.keys() if c not in ANNOUNCE_CATS
        ]

        rows = "".join(
            f"<tr><th class='text-nowrap' style='width:9rem'>{cat}</th><td>{ann_list(by_cat.get(cat, []))}</td></tr>"
            for cat in ordered_cats
        )
        announce_block = (
            "<h4 class='mt-1'> 公告區</h4>"
            "<div class='table-responsive'><table class='table table-sm align-middle'>"
            "<thead><tr><th style='width:9rem'>分類</th><th>內容</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )

    
    hw_block = render_homework_table(hw_window)

    
    reading_block = ""
    try:
        if current_user.role == "student":
            ws, we = _week_range(today())
            week_pts, week_sdg = _user_week_points(current_user.id, ws, we)
            delta, nxt, curw   = _next_badge_delta(current_user.id, today())
            streak             = _streak_weeks(current_user.id, today())

            
            q_read = CompletedTask.query.join(Task, CompletedTask.task_id == Task.id).filter(  
                Task.task_type == "mission"
            )
            q_read = ct_user_filter(q_read, current_user.id)  
            total_pts = 0
            for ct in q_read.all():
                if _ct_status(ct) == "approved":  
                    total_pts += _safe_int(_ct_load(ct).get("score_total", 0), 0)  
            level = _level_of(total_pts)  

            target_tip = (
                f"本週 {curw} 分，距離 {nxt} 分徽章還差 <b>{delta}</b> 分！"
                "保持連續週數可再拿 +2（本月封頂 +6）。"
            )

            reading_block = f"""
            <h4 class='mt-4'> 永續閱讀儀表板</h4>
            <div class='card border-0 shadow-sm mb-3'>
              <div class='card-body'>
                <div class='row'>
                  <div class='col-md-4'>
                    <div class='alert alert-success mb-2'>本週分數：<b>{week_pts}</b></div>
                  </div>
                  <div class='col-md-4'>
                    <div class='alert alert-warning mb-2'>連續週數：<b>{streak}</b></div>
                  </div>
                  <div class='col-md-4'>
                    <div class='alert alert-info mb-2'>等級：<b>{level}</b>（歷史 {total_pts} 分）</div>
                  </div>
                </div>
                <div class='small text-muted mb-2'>{target_tip}</div>
                <a class='btn btn-sm btn-outline-primary' href='/reading/dashboard'>前往完整閱讀儀表板</a>
              </div>
            </div>
            """
    except Exception:
        reading_block = ""

    
    diary_block = ""
    if include_diary and current_user.role in ("student", "teacher"):
        prompt = (DiaryPrompt.query.filter_by(unit=unit, grade=grade, class_no=class_no, date=dsel)  
                                 .order_by(DiaryPrompt.id.desc()).first())
        if prompt:
            sub = None
            if current_user.role == "student":
                sub = DiarySubmission.query.filter_by(prompt_id=prompt.id, student_name=current_user.username).first()  

            def _safe_get(obj, *names):
                for n in names:
                    if hasattr(obj, n):
                        v = getattr(obj, n)
                        if isinstance(v, str) and v.strip():
                            return v.strip()
                return None

            def _render_feedback_cards(feedbacks):
                items = []
                for n in feedbacks:
                    body = _safe_get(n, "content", "description", "body", "text", "comment", "feedback")
                    if not body or not str(body).strip():
                        continue
                    author = display_name_of(_safe_get(n, "created_by", "teacher_name") or "teacher")
                    body = _user_text_html(str(body))
                    items.append(
                        "<div class='mb-2 p-2 border rounded bg-white'>"
                        f"<div class='small text-muted'>來自：{author}</div>"
                        f"<div class='mt-1'>{body}</div>"
                        "</div>"
                    )
                return "" if not items else "<div class='mt-3'><div class='h6 mb-2'>老師的日記回饋</div>" + "".join(items) + "</div>"

            fb_html = ""
            DiaryFeedback = globals().get("DiaryFeedback")
            if DiaryFeedback is not None:
                q = DiaryFeedback.query
                conds = []
                if hasattr(DiaryFeedback, "prompt_id"):
                    conds.append(DiaryFeedback.prompt_id == prompt.id)
                elif hasattr(DiaryFeedback, "diary_prompt_id"):
                    conds.append(DiaryFeedback.diary_prompt_id == prompt.id)
                stu_col = None
                for name in ("student_name", "target_student", "student_username"):
                    if hasattr(DiaryFeedback, name):
                        stu_col = getattr(DiaryFeedback, name)
                        break
                if current_user.role == "student" and stu_col is not None:
                    conds.append(stu_col == current_user.username)
                if hasattr(DiaryFeedback, "date"):
                    conds.append(DiaryFeedback.date == dsel)
                if hasattr(DiaryFeedback, "unit"):
                    conds.append(DiaryFeedback.unit == unit)
                if hasattr(DiaryFeedback, "grade"):
                    conds.append(DiaryFeedback.grade == grade)
                if hasattr(DiaryFeedback, "class_no"):
                    conds.append(DiaryFeedback.class_no == class_no)
                if conds:
                    q = q.filter(*conds)
                if hasattr(DiaryFeedback, "created_at"):
                    q = q.order_by(DiaryFeedback.created_at.asc())
                else:
                    q = q.order_by(DiaryFeedback.id.asc())
                feedbacks = q.all()
                fb_html = _render_feedback_cards(feedbacks)

            if not fb_html and sub is not None:
                body = _safe_get(sub, "teacher_feedback", "teacher_comment", "comment", "feedback")
                if body and str(body).strip():
                    fake = type("X", (), {
                        "title": "老師回饋",
                        "created_by": getattr(sub, "reviewed_by", None) or getattr(sub, "teacher_name", None) or "teacher",
                        "content": str(body)
                    })
                    fb_html = _render_feedback_cards([fake])

            if not fb_html and "CommNote" in globals():
                CN = globals()["CommNote"]
                q = (CN.query.filter(CN.unit==unit, CN.date==dsel, CN.author_role=="teacher"))
                if hasattr(CN, "student_username") and current_user.role == "student":
                    q = q.filter(CN.student_username == current_user.username)
                if hasattr(CN, "visible_to_student"):
                    q = q.filter(CN.visible_to_student == 1)
                q = q.order_by(CN.id.asc())
                feedbacks = q.all()
                fb_html = _render_feedback_cards(feedbacks)

            if current_user.role == "student":
                lock_on = current_app.config.get("LOCK_PAST_DATES", True)
                is_locked = bool(lock_on and prompt.date and today() > prompt.date)
                btn = "<span class='badge bg-secondary'>已截止</span>" if is_locked else \
                      f"<a class='btn btn-sm btn-outline-primary' href='/diary_write/{prompt.id}'>{'修改日記' if sub else '寫日記'}</a>"
                state = ("<span class='badge bg-success ms-2'>已繳交</span>" if sub else "<span class='badge bg-secondary ms-2'>未繳交</span>")
                prompt_thumb = img_html(diary_prompt_img_name(prompt), maxw=260)
                thumb = img_html(getattr(sub, "image", None), maxw=260) if sub else ""
                diary_block = (
                    "<h4 class='mt-4'>日記區</h4>"
                    "<div class='p-3 border rounded'>"
                    f"<div class='fw-bold'>{prompt.date}｜{prompt.title}{state}</div>"
                    f"{('<div class=\"small text-muted mt-1\">'+ _user_text_html(prompt.instruction) +'</div>') if prompt.instruction else ''}"
                    f"{prompt_thumb}"
                    f"{thumb}"
                    f"<div class='mt-2'>{btn}</div>"
                    f"{fb_html}"
                    "</div>"
                )
            else:
                total = db.session.query(func.count(DiarySubmission.id)).filter_by(prompt_id=prompt.id).scalar() or 0  
                pub   = db.session.query(func.count(DiarySubmission.id)).filter_by(prompt_id=prompt.id, share_to_parent=1).scalar() or 0
                diary_block = (
                    "<h4 class='mt-4'>日記區</h4>"
                    "<div class='p-3 border rounded'>"
                    f"<div class='fw-bold'>{prompt.date}｜{prompt.title}</div>"
                    f"{('<div class=\"small text-muted mt-1\">'+ _user_text_html(prompt.instruction) +'</div>') if prompt.instruction else ''}"
                    f"{img_html(diary_prompt_img_name(prompt), maxw=260)}"
                    f"<div class='mt-2 small text-muted'>繳交：{total}　公開：{pub}</div>"
                    "<div class='mt-2'><a class='btn btn-sm btn-outline-primary' href='/teacher_diary'>管理日記題目</a></div>"
                    "</div>"
                )
    elif include_diary and current_user.role == "parent":
        today_ = dsel
        links  = parent_child_links()
        cards  = []
        for lk in links:
            stu = User.query.filter_by(username=lk.student_name, role="student").first()
            if not stu:
                continue
            pro = (DiaryPrompt.query.filter_by(unit=stu.unit, grade=stu.grade, class_no=stu.class_no, date=today_)
                               .order_by(DiaryPrompt.id.desc()).first())
            if not pro:
                continue
            sub = DiarySubmission.query.filter_by(prompt_id=pro.id, student_name=stu.username).first()
            if not sub:
                state = "<span class='badge bg-secondary'>未繳交</span>"; thumb = ""
            elif not sub.share_to_parent:
                state = "<span class='badge bg-warning text-dark'>孩子未公開</span>"; thumb = ""
            else:
                state = "<span class='badge bg-success'>可查看</span>"
                thumb = img_html(getattr(sub, "image", None), maxw=180) if getattr(sub, "image", None) else ""
            prompt_thumb = img_html(diary_prompt_img_name(pro), maxw=180)
            cards.append(
                "<div class='card mb-2'><div class='card-body'>"
                f"<div class='h6 mb-1'>{stu.display_name or stu.username}</div>"
                f"<div class='small text-muted mb-1'>題目：{pro.title}</div>"
                f"{state}　<a class='btn btn-sm btn-outline-primary ms-2' href='/parent?date={dsel.isoformat()}#parent-diary-panel'>前往查看</a>"
                f"{prompt_thumb}"
                f"{thumb}"
                "</div></div>"
            )
        if cards:
            diary_block = "<h4 class='mt-4'>日記區</h4>" + "".join(cards)

    medication_block = ""
    try:
        med_q = MedicationRecord.query.filter(MedicationRecord.date == dsel)
        if current_user.role == "parent":
            kids = parent_child_student_usernames()
            med_q = med_q.filter(MedicationRecord.student_name.in_(kids)) if kids else med_q.filter(text("0=1"))
        elif current_user.role == "student":
            med_q = med_q.filter(MedicationRecord.student_name == current_user.username)
        elif current_user.role == "teacher":
            med_q = med_q.filter(
                MedicationRecord.unit == unit,
                MedicationRecord.grade == grade,
                MedicationRecord.class_no == class_no,
            )
        else:
            med_q = med_q.filter(MedicationRecord.unit == (unit or SCHOOL_NAME))
        meds = med_q.order_by(MedicationRecord.id.desc()).limit(5).all()
        if meds:
            def med_mini(rec: "MedicationRecord"):
                return (
                    "<li class='list-group-item'>"
                    f"<div class='fw-bold'>{rec.student_display or rec.student_name}｜{escape(rec.medicine_name)} {_status_badge_map(rec.status, MEDICATION_STATUS_OPTIONS)}</div>"
                    f"<div class='small text-muted'>時間：{escape(rec.time_note or '未指定')}　劑量：{escape(rec.dose or '未填')}</div>"
                    "</li>"
                )
            medication_block = (
                "<h4 class='mt-4'>用藥紀錄</h4>"
                f"<ul class='list-group'>{''.join(med_mini(m) for m in meds)}</ul>"
                "<div class='mt-2'><a class='btn btn-sm btn-outline-secondary' href='/medication'>查看完整用藥紀錄</a></div>"
            )
    except Exception:
        medication_block = ""

    comm_block = ""
    if "CommNote" in globals():
        all_today = (
            CommNote.query  
            .filter(CommNote.unit == unit, CommNote.date == dsel)
            .order_by(CommNote.id.desc())
            .all()
        )

        def visible_notes_for_home(notes):
            role = current_user.role

            
            if role == "teacher":
                my_g = str(grade or "")
                my_c = str(class_no or "")
                out = []
                for n in notes:
                    ng = str(getattr(n, "grade", "") or "")
                    nc = str(getattr(n, "class_no", "") or "")
                    
                    if ng and nc and (ng != my_g or nc != my_c):
                        continue
                    out.append(n)
                return out

            
            
            
            elif role == "student":
                mine = current_user.username
                my_g = str(grade or "")
                my_c = str(class_no or "")
                out = []
                for n in notes:
                    stu_un = getattr(n, "student_username", None)

                    
                    if stu_un and stu_un == mine:
                        if (
                            n.author_role in ("teacher", "parent")
                            and hasattr(n, "visible_to_student")
                            and n.visible_to_student != 1
                        ):
                            continue
                        out.append(n)
                        continue

                    
                    ng = str(getattr(n, "grade", "") or "")
                    nc = str(getattr(n, "class_no", "") or "")
                    if (not stu_un) and n.author_role == "teacher" and ng == my_g and nc == my_c:
                        if hasattr(n, "visible_to_student") and n.visible_to_student == 0:
                            continue
                        out.append(n)
                return out

            
            
            elif role == "parent":
                kids = parent_child_student_usernames()
                if not kids:
                    return []
                kids_set = set(kids)
                out = []
                for n in notes:
                    stu_un = getattr(n, "student_username", None)
                    if not stu_un or stu_un not in kids_set:
                        continue

                    
                    if (
                        n.author_role == "student"
                        and hasattr(n, "visible_to_parent")
                        and n.visible_to_parent != 1
                    ):
                        continue

                    
                    if n.author_role == "parent" and n.created_by != current_user.username:
                        continue

                    out.append(n)
                return out

            return []

        vis_notes = visible_notes_for_home(all_today)
        if vis_notes:
            role_badge = {
                "teacher": "<span class='badge bg-primary ms-1'>老師</span>",
                "parent": "<span class='badge bg-dark ms-1'>家長</span>",
                "student": "<span class='badge bg-success ms-1'>學生</span>",
            }

            def mini(n: "CommNote"):
                target = (
                    f"指定：{display_name_of(n.student_username)}"
                    if getattr(n, "student_username", None)
                    else "（未指定）"
                )
                return (
                    "<li class='list-group-item'>"
                    f"<div class='fw-bold'>{n.title} {role_badge.get(n.author_role, '')}</div>"
                    f"<div class='small text-muted'>{target} · {display_name_of(n.created_by)}</div>"
                    f"{img_html(getattr(n, 'image', None), maxw=240)}"
                    "</li>"
                )

            top3 = vis_notes[:3]
            comm_block = (
                "<h4 class='mt-4'>交流區</h4>"
                f"<ul class='list-group'>{''.join(mini(n) for n in top3)}</ul>"
            )

    
    if not (announce_block or hw_block or reading_block or diary_block or medication_block or comm_block):
        empty = "<div class='text-center text-muted py-4'>今天沒有聯絡簿內容</div>"
        return subtitle + toolbar + nav + empty

    return (
        subtitle + toolbar + nav
        + (announce_block or "")
        + (hw_block or "")
        + (reading_block or "")
        + (diary_block or "")
        + (medication_block or "")
        + (comm_block or "")
    )

@app.route("/parent")
@roles_required("parent")
def parent():
    """
    家長首頁（優化版 UI）：
    - 上方：家長總覽與日常服務
    - 下方：孩子閱讀概況直接整合完整儀表板資訊
    """
    from sqlalchemy import func
    from collections import defaultdict

    parent_name = current_user.display_name or current_user.username
    today_d = _today()
    today_iso = today_d.isoformat()
    selected_d = parse_date((request.args.get("date") or "").strip()) or today_d
    selected_iso = selected_d.isoformat()
    selected_word = "今日" if selected_d == today_d else "這一天"
    ws, we = _week_range(today_d)
    week_range_txt = f"{ws.strftime('%m/%d')} - {we.strftime('%m/%d')}"

    def safe_url_for(endpoint, **values):
        try:
            return url_for(endpoint, **values)
        except Exception:
            if endpoint == "reading_parent_coread" and "ct_id" in values:
                return f"/reading/parent_coread/{values['ct_id']}"
            return "#"

    def need_parent_coread(ct, payload: dict) -> bool:
        return reading_need_parent_coread(ct=ct, payload=payload, task=getattr(ct, "task", None))

    def parent_coread_done(payload: dict) -> bool:
        return reading_parent_coread_done(payload)

    def status_badge(st: str) -> str:
        if st == "approved":
            return "<span class='badge bg-success'>已通過</span>"
        if st == "pending":
            return "<span class='badge bg-warning text-dark'>待審核</span>"
        return "<span class='badge bg-danger'>已退回</span>"

    def kpi_tile(label, value, sub=""):
        sub_html = f"<div class='kpi-sub'>{sub}</div>" if sub else ""
        return (
            "<div class='ui-kpi'>"
            f"<div class='kpi-label'>{label}</div>"
            f"<div class='kpi-value'>{value}</div>"
            f"{sub_html}"
            "</div>"
        )

    def goal_int(v, default):
        return int(v) if isinstance(v, (int, float)) else default

    def pct(cur, goal):
        if not goal or goal <= 0:
            return 0
        return max(0, min(100, round(cur * 100 / goal)))

    week_goal = goal_int(globals().get("READING_WEEK_GOAL", 30), 30)
    month_goal = goal_int(globals().get("READING_MONTH_GOAL", 120), 120)
    term_goal = goal_int(globals().get("READING_TERM_GOAL", 300), 300)
    month_start, month_end = _month_range(today_d)

    def today_diary_card(stu):
        if not stu:
            return None
        stu_name = stu.display_name or stu.username
        sign_link = safe_url_for("parent_sign", student=stu.username, date=selected_iso)
        date_word = selected_word

        try:
            prompt = (
                DiaryPrompt.query.filter_by(
                    unit=stu.unit,
                    grade=stu.grade,
                    class_no=stu.class_no,
                    date=selected_d,
                )
                .order_by(DiaryPrompt.id.desc())
                .first()
            )
        except Exception:
            prompt = None

        sub = (
            DiarySubmission.query.filter_by(prompt_id=prompt.id, student_name=stu.username).first()
            if prompt
            else None
        )
        shared = bool(sub and getattr(sub, "share_to_parent", 0) == 1)

        if prompt:
            diary_heading = f"{escape(prompt.title or '日記題目')}"
            diary_status = "<span class='badge bg-success ms-1'>可查看內容</span>" if shared else "<span class='badge bg-warning text-dark ms-1'>尚未公開</span>"
            diary_instruction = (
                f"<div class='journal-prompt-note'>{_user_text_html(prompt.instruction)}</div>"
                if getattr(prompt, "instruction", None)
                else "<div class='journal-prompt-note journal-prompt-note--empty'>老師沒有補充說明。</div>"
            )
            prompt_thumb = img_html(diary_prompt_img_name(prompt), maxw=240)
            prompt_block = (
                "<div class='journal-prompt-card'>"
                "<div class='journal-panel-title'>老師題目</div>"
                f"<div class='journal-heading'>{diary_heading}</div>"
                f"{diary_instruction}"
                f"{prompt_thumb}"
                "</div>"
            )
            if shared:
                text_html = _user_text_html((sub.content or "").strip())
                img_name = getattr(sub, "image", None)
                img_html_block = img_html(img_name, maxw=320) if img_name else ""
                diary_content = (
                    "<div class='journal-entry-card'>"
                    "<div class='journal-panel-title'>孩子日記</div>"
                    "<div class='journal-entry-text text-break'>"
                    f"{text_html or '孩子沒有填寫文字內容。'}"
                    "</div>"
                    f"{img_html_block or ''}"
                    "</div>"
                )
            else:
                if sub:
                    diary_content = (
                        "<div class='journal-locked-card'>"
                        "<div class='journal-panel-title'>孩子日記</div>"
                        "<strong>孩子已繳交，尚未開放家長閱讀</strong>"
                        f"<span>您仍可先查看老師{date_word}發布的題目與說明。</span>"
                        "</div>"
                    )
                else:
                    diary_content = (
                        "<div class='journal-locked-card'>"
                        "<div class='journal-panel-title'>孩子日記</div>"
                        "<strong>孩子尚未繳交這篇日記</strong>"
                        "<span>完成後會在這裡顯示狀態。</span>"
                        "</div>"
                    )
            diary_block = prompt_block + diary_content
        else:
            diary_heading = ""
            diary_status = ""
            diary_block = f"<div class='parent-empty'>{date_word}老師尚未發布日記題目。</div>"

        need_hw = False
        try:
            q_hw = Task.query.filter(Task.unit == (stu.unit or ""))
            q_hw = q_hw.filter(
                (Task.is_school_wide == 1)
                | ((Task.grade == stu.grade) & (Task.class_no == stu.class_no))
            )
            need_hw = (
                q_hw.filter(
                    ((Task.task_type == "homework") | (Task.task_type == None)),
                    ((Task.start_date == None) | (Task.start_date <= selected_d)),
                    ((Task.end_date == None) | (Task.end_date >= selected_d)),
                ).first()
                is not None
            )
        except Exception:
            need_hw = False

        need_sign_today = bool(prompt) or need_hw
        try:
            sig = signature_of(current_user.username, stu.username, selected_d, "homework")
        except Exception:
            sig = None

        if sig:
            sign_status = "<span class='badge bg-success ms-1'>已簽名</span>"
            sign_hint = f"已完成{date_word}簽名；若需更新可再次送出。"
            if getattr(sig, "image", None):
                sig_img = img_html(sig.image, maxw=320)
            else:
                sig_img = ""
            sign_btn_label = "重新簽名"
        else:
            if need_sign_today:
                sign_status = "<span class='badge bg-warning text-dark ms-1'>未簽名</span>"
                sign_hint = f"{date_word}需簽名，請協助孩子完成。"
                sign_btn_label = "去簽名"
            else:
                sign_status = ""
                sign_hint = ""
                sign_btn_label = ""
            sig_img = ""

        sign_panel = ""
        if need_sign_today or sig:
            signature_media = f"<div class='journal-panel-body mt-3'>{sig_img}</div>" if sig_img else ""
            sign_panel = f"""
            <div class='journal-panel journal-panel--sign parent-sign-card'>
              <div class='parent-sign-head'>
                <div class='journal-panel-title mb-0'>家長簽名</div>
                <div>{sign_status}</div>
              </div>
              <div class='parent-muted mb-2'>{sign_hint}</div>
              <a class='btn parent-soft-btn parent-soft-btn--green' href='{sign_link}'>{sign_btn_label}</a>
              {signature_media}
            </div>
            """
        heading_html = f"<div class='journal-heading'>{diary_heading}</div>" if diary_heading else ""
        card_html = f"""
        <div class='parent-diary-inline'>
          <div class='parent-diary-inline-head'>
            <div>
              {heading_html}
            </div>
            <div class='parent-badge-stack'>{diary_status}</div>
          </div>
          <div class='journal-panel journal-panel--diary'>
            <div class='journal-panel-body'>{diary_block}</div>
          </div>
        </div>
        """
        return Markup(card_html), Markup(sign_panel), bool(need_sign_today and not sig), bool(shared)

    comm_url = safe_url_for("comm")

    
    links = parent_child_links()
    kids = []
    for lk in links:
        stu = User.query.filter_by(username=getattr(lk, "student_name", ""), role="student").first()
        if stu:
            kids.append(stu)

    diary_info_map = {}
    need_sign_total = 0
    shared_diary_total = 0
    for stu in kids:
        info = today_diary_card(stu)
        if not info:
            continue
        diary_info_map[stu.username] = info
        _diary_html, _sign_html, need_sign, shared = info
        if need_sign:
            need_sign_total += 1
        if shared:
            shared_diary_total += 1

    default_student = kids[0].username if kids else None
    primary_sign = "#"
    if kids:
        primary_sign = safe_url_for("parent_sign", student=default_student, date=today_iso)
        if len(kids) == 1:
            sign_controls = f"<a class='btn btn-sm btn-outline-success' href='{primary_sign}'>今日簽名</a>"
        else:
            dropdown = "".join(
                f"<li><a class='dropdown-item' href='{safe_url_for('parent_sign', student=stu.username, date=today_iso)}'>"
                f"{stu.display_name or stu.username}</a></li>"
                for stu in kids
            )
            sign_controls = (
                "<div class='btn-group'>"
                f"<a class='btn btn-sm btn-outline-success' href='{primary_sign}'>今日簽名</a>"
                "<button class='btn btn-sm btn-outline-success dropdown-toggle dropdown-toggle-split' "
                "data-bs-toggle='dropdown' aria-expanded='false'></button>"
                f"<ul class='dropdown-menu dropdown-menu-end'>{dropdown}</ul>"
                "</div>"
            )
    else:
        sign_controls = "<button class='btn btn-sm btn-outline-secondary' disabled>今日簽名</button>"


    def parent_homework_cards(tasks):
        hw_items = _filter_homework_items(tasks)
        if not hw_items:
            return ""
        return "<div class='parent-homework-table'>" + render_homework_table(hw_items, heading=None, show_empty=False) + "</div>"

    def parent_homework_diary_table(stu, tasks):
        hw_items = _filter_homework_items(tasks)
        preferred = PREFERRED_SUBJECT_ORDER
        base_order = [s for s in preferred if s in CATEGORY_OPTIONS] + [c for c in CATEGORY_OPTIONS if c not in preferred]
        by_cat = {}
        for t in hw_items:
            by_cat.setdefault(t.category or "其他", []).append(t)
        present_order = [c for c in base_order if c in by_cat] + sorted([c for c in by_cat.keys() if c not in base_order])

        def hw_cell(items):
            out = []
            for t in items:
                deadline = f"<small class='text-muted ms-2'>截止：{t.end_date}</small>" if t.end_date else ""
                thumb = img_html(task_img_name(t), maxw=280)
                out.append(
                    "<div class='parent-homework-card'>"
                    f"<div class='parent-homework-title'>{escape(t.title)}</div>"
                    f"<div class='parent-homework-desc'>{_desc_clean_html(t.description or '')}</div>"
                    f"{thumb}"
                    f"<div class='parent-homework-meta'>教師：{escape(display_name_of(t.created_by) or '')}{deadline}</div>"
                    "</div>"
                )
            return "".join(out)

        rows = "".join(
            "<article class='parent-work-row'>"
            f"<div class='parent-work-label'>{cat}</div>"
            f"<div class='parent-work-content'>{hw_cell(by_cat.get(cat, []))}</div>"
            "</article>"
            for cat in present_order
        )
        if not rows:
            empty_hw = "今天沒有作業" if selected_d == today_d else "這一天沒有作業"
            rows = (
                "<article class='parent-work-row parent-work-row--empty'>"
                "<div class='parent-work-label'>作業</div>"
                f"<div class='parent-work-content'><div class='parent-empty parent-empty--soft'>{empty_hw}</div></div>"
                "</article>"
            )

        diary_info = diary_info_map.get(stu.username)
        diary_html = str(diary_info[0]) if diary_info else "<div class='parent-empty'>今天老師尚未發布日記題目。</div>"
        sign_html = str(diary_info[1]) if diary_info else ""
        rows += (
            "<article class='parent-work-row parent-work-row--diary'>"
            "<div class='parent-work-label'>日記</div>"
            f"<div class='parent-work-content'>{diary_html}</div>"
            "</article>"
        )
        if sign_html:
            rows += (
                "<article class='parent-work-row parent-work-row--sign'>"
                "<div class='parent-work-label'>家長簽名</div>"
                f"<div class='parent-work-content'>{sign_html}</div>"
                "</article>"
            )
        return (
            "<div class='parent-homework-diary-board'>"
            f"{rows}"
            "</div>"
        )

    def parent_announce_cards(tasks):
        cards = []
        for t in (tasks or [])[:6]:
            thumb = img_html(task_img_name(t), maxw=240)
            cards.append(
                "<article class='parent-mini-item'>"
                f"<div class='fw-bold'>{escape(t.title)}</div>"
                f"<div class='parent-muted'>{escape(t.category or '公告')} · {escape(display_name_of(t.created_by) or '')}</div>"
                f"<div class='mt-1'>{_desc_clean_html(t.description or '')}</div>"
                f"{thumb}"
                "</article>"
            )
        return "".join(cards)

    def parent_med_cards(stu):
        try:
            meds = (
                MedicationRecord.query.filter(
                    MedicationRecord.date == selected_d,
                    MedicationRecord.student_name == stu.username,
                )
                .order_by(MedicationRecord.id.desc())
                .limit(4)
                .all()
            )
        except Exception:
            meds = []
        return "".join(
            "<article class='parent-mini-item'>"
            f"<div class='fw-bold'>{escape(m.medicine_name)} {_status_badge_map(m.status, MEDICATION_STATUS_OPTIONS)}</div>"
            f"<div class='parent-muted'>時間：{escape(m.time_note or '未指定')} · 劑量：{escape(m.dose or '未填')}</div>"
            "</article>"
            for m in meds
        )

    def parent_note_cards(stu):
        if "CommNote" not in globals():
            return ""
        try:
            raw_notes = (
                CommNote.query.filter(CommNote.unit == stu.unit, CommNote.date == selected_d)
                .order_by(CommNote.id.desc())
                .all()
            )
        except Exception:
            raw_notes = []
        notes = []
        for n in raw_notes:
            stu_un = getattr(n, "student_username", None)
            author_role = getattr(n, "author_role", "")
            if stu_un:
                if stu_un != stu.username:
                    continue
                if author_role == "student" and getattr(n, "visible_to_parent", 1) != 1:
                    continue
                if author_role == "teacher" and getattr(n, "visible_to_parent", 1) == 0:
                    continue
                if author_role == "parent" and getattr(n, "created_by", "") != current_user.username:
                    continue
                notes.append(n)
                continue
            if (
                author_role == "teacher"
                and str(getattr(n, "grade", "") or "") == str(stu.grade or "")
                and str(getattr(n, "class_no", "") or "") == str(stu.class_no or "")
                and getattr(n, "visible_to_parent", 1) != 0
            ):
                notes.append(n)

        return "".join(
            "<article class='parent-mini-item'>"
            f"<div class='fw-bold'>{escape(n.title)}</div>"
            f"<div class='parent-muted'>{escape(display_name_of(n.created_by) or '')}</div>"
            f"<div class='mt-1'>{_desc_clean_html(getattr(n, 'content', '') or '')}</div>"
            f"{img_html(getattr(n, 'image', None), maxw=240)}"
            "</article>"
            for n in notes[:4]
        )

    def parent_today_contactbook(students):
        child_cards = []
        for stu in students:
            base = Task.query.filter(Task.unit == (stu.unit or "")).filter(
                (Task.is_school_wide == 1)
                | ((Task.grade == stu.grade) & (Task.class_no == stu.class_no))
            )
            ann_window = (
                base.filter(
                    Task.task_type == "announce",
                    ((Task.start_date == None) | (Task.start_date <= selected_d)),
                    ((Task.end_date == None) | (Task.end_date >= selected_d)),
                )
                .order_by(Task.id.asc())
                .all()
            )
            hw_window = (
                base.filter(
                    ((Task.task_type == "homework") | (Task.task_type == None)),
                    ((Task.start_date == None) | (Task.start_date <= selected_d)),
                    ((Task.end_date == None) | (Task.end_date >= selected_d)),
                )
                .order_by(Task.id.asc())
                .all()
            )

            sections = []
            ann_html = parent_announce_cards(ann_window)
            homework_diary_html = parent_homework_diary_table(stu, hw_window)
            med_html = parent_med_cards(stu)
            note_html = parent_note_cards(stu)

            def add_section(title, html, *, always=False):
                if html or always:
                    sections.append(
                        "<div class='parent-today-section'>"
                        f"<div class='parent-subtitle'>{title}</div>"
                        f"{html}"
                        "</div>"
                    )

            add_section("公告", ann_html)
            add_section("作業與日記簽名", homework_diary_html, always=True)
            add_section("用藥紀錄", med_html)
            add_section("交流訊息", note_html)

            child_cards.append(
                "<article class='parent-child-contactbook'>"
                "<div class='parent-child-contactbook-head'>"
                "<div>"
                f"<div class='parent-child-name'>{escape(stu.display_name or stu.username)}</div>"
                f"<div class='parent-muted'>{escape(stu.unit or '')} · {escape(str(stu.grade or ''))}年{escape(str(stu.class_no or ''))}班</div>"
                "</div>"
                f"<span class='parent-date-pill'>{selected_iso}</span>"
                "</div>"
                f"{''.join(sections)}"
                "</article>"
            )
        if child_cards:
            return "<div class='parent-today-list'>" + "".join(child_cards) + "</div>"
        empty_text = "今天沒有需要處理的聯絡簿內容。" if selected_d == today_d else "這一天沒有需要處理的聯絡簿內容。"
        return f"<div class='parent-empty parent-empty--panel'>{empty_text}</div>"

    prev_d = (selected_d - timedelta(days=1)).isoformat()
    next_d = (selected_d + timedelta(days=1)).isoformat()
    selected_label = "今天" if selected_d == today_d else f"查看 {selected_iso}"
    parent_date_nav = (
        "<div class='parent-date-nav'>"
        f"<a class='btn parent-soft-btn' href='{safe_url_for('parent', date=prev_d)}'>前一天</a>"
        f"<form method='get' action='{safe_url_for('parent')}' class='parent-date-form'>"
        f"<input type='date' name='date' class='form-control' value='{selected_iso}'>"
        "<button class='btn parent-soft-btn parent-soft-btn--green'>前往</button>"
        "</form>"
        f"<a class='btn parent-soft-btn' href='{safe_url_for('parent', date=today_iso)}'>今天</a>"
        f"<a class='btn parent-soft-btn' href='{safe_url_for('parent', date=next_d)}'>下一天</a>"
        "</div>"
    )
    parent_diary_date_nav = (
        "<div class='parent-date-nav parent-diary-date-nav'>"
        f"<a class='btn parent-soft-btn' href='{safe_url_for('parent', date=prev_d)}#parent-diary-panel'>前一天</a>"
        f"<form method='get' action='{safe_url_for('parent')}#parent-diary-panel' class='parent-date-form'>"
        f"<input type='date' name='date' class='form-control' value='{selected_iso}'>"
        "<button class='btn parent-soft-btn parent-soft-btn--green'>前往</button>"
        "</form>"
        f"<a class='btn parent-soft-btn' href='{safe_url_for('parent', date=today_iso)}#parent-diary-panel'>今天</a>"
        f"<a class='btn parent-soft-btn' href='{safe_url_for('parent', date=next_d)}#parent-diary-panel'>下一天</a>"
        "</div>"
    )

    contactbook_block = (
        "<section class='parent-panel parent-contactbook' id='parent-diary-panel'>"
        "<div class='parent-panel-head'>"
        "<div>"
        "<div class='parent-section-kicker'>Today</div>"
        "<h4>今日作業、公告與日記簽名</h4>"
        f"<div class='parent-muted mt-1'>{selected_label}</div>"
        "</div>"
        f"{parent_date_nav}"
        "</div>"
        "<div class='parent-panel-body'>"
        f"{parent_today_contactbook(kids)}"
        "</div>"
        "</section>"
    )

    page_style = """
    <style>
    .parent-page{display:flex;flex-direction:column;gap:1.1rem;}
    .parent-hero{position:relative;overflow:hidden;border-radius:30px;padding:1.4rem;background:linear-gradient(135deg,#ecfdf3 0%,#fff8e8 56%,#f4fbf8 100%);border:1px solid rgba(35,92,59,.1);box-shadow:0 24px 60px rgba(24,76,46,.12);}
    .parent-hero:before{content:"";position:absolute;right:-5rem;top:-5.5rem;width:18rem;height:18rem;border-radius:50%;background:rgba(90,167,113,.16);}
    .parent-hero-inner{position:relative;display:flex;justify-content:space-between;gap:1.2rem;align-items:flex-start;flex-wrap:wrap;}
    .parent-eyebrow,.parent-section-kicker{font-size:.78rem;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#4f8f61;margin-bottom:.25rem;}
    .parent-hero h3,.parent-panel h4{margin:0;color:#162318;font-weight:900;letter-spacing:-.02em;}
    .parent-muted{color:#647067;font-size:.94rem;}
    .parent-hero-actions{display:flex;gap:.55rem;align-items:center;flex-wrap:wrap;justify-content:flex-end;}
    .parent-soft-btn{border-radius:999px!important;border:1px solid rgba(35,92,59,.18)!important;background:#fff!important;color:#244c32!important;font-weight:800!important;padding:.5rem .95rem!important;box-shadow:0 10px 24px rgba(24,76,46,.08);}
    .parent-soft-btn:hover{background:#f3fbf5!important;color:#16351f!important;transform:translateY(-1px);}
    .parent-soft-btn--green{background:#34784a!important;color:#fff!important;border-color:#34784a!important;}
    .parent-soft-btn--green:hover{background:#28613b!important;color:#fff!important;}
    .parent-metric-grid{position:relative;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;margin-top:1.1rem;}
    .parent-hero .hero-metric{background:rgba(255,255,255,.86);border:1px solid rgba(35,92,59,.1);border-radius:20px;padding:.95rem 1rem;box-shadow:0 14px 32px rgba(24,76,46,.08);}
    .parent-hero .hero-metric-value{font-size:1.65rem;line-height:1;font-weight:900;color:#183d28;margin-bottom:.35rem;}
    .parent-hero .hero-metric-label{font-size:.88rem;color:#2f4738;font-weight:800;}
    .parent-hero .hero-metric-sub{font-size:.78rem;color:#7b887f;margin-top:.12rem;}
	    .parent-main-grid{display:grid;grid-template-columns:1fr;gap:1rem;align-items:start;}
    .parent-panel{background:rgba(255,255,255,.97);border:1px solid rgba(19,56,35,.08);border-radius:28px;box-shadow:0 22px 54px rgba(24,76,46,.1);overflow:hidden;}
    .parent-panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;padding:1.15rem 1.25rem;border-bottom:1px solid rgba(19,56,35,.08);background:linear-gradient(180deg,#ffffff 0%,#fbfdf8 100%);}
    .parent-panel-body{padding:1.1rem 1.25rem;}
    .parent-today-list{display:flex;flex-direction:column;gap:.9rem;}
    .parent-child-contactbook{border-radius:22px;border:1px solid rgba(19,56,35,.08);background:linear-gradient(180deg,#fff 0%,#fbfff8 100%);padding:1rem;box-shadow:0 14px 30px rgba(24,76,46,.07);}
    .parent-child-contactbook-head{display:flex;justify-content:space-between;gap:.8rem;align-items:flex-start;flex-wrap:wrap;margin-bottom:.85rem;}
    .parent-today-section{border-radius:20px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;margin-bottom:.8rem;}
    .parent-today-section:last-child{margin-bottom:0;}
    .parent-subtitle{font-weight:900;color:#183d28;margin-bottom:.65rem;}
    .parent-mini-item,.parent-task-item{background:#fff;border:1px solid rgba(19,56,35,.08);border-radius:16px;padding:.85rem;margin-bottom:.6rem;}
    .parent-mini-item:last-child,.parent-task-item:last-child{margin-bottom:0;}
    .parent-task-top{display:flex;justify-content:space-between;gap:.8rem;align-items:flex-start;flex-wrap:wrap;margin-bottom:.45rem;}
    .parent-task-title{font-weight:900;color:#17231b;font-size:1.02rem;}
    .parent-task-desc{color:#46524a;font-size:.93rem;line-height:1.65;margin-top:.35rem;}
    .parent-task-meta{margin-top:.65rem;color:#647067;font-size:.9rem;font-weight:800;}
    .parent-homework-table .table{margin-bottom:0;background:#fff;border-radius:16px;overflow:hidden;}
    .parent-homework-table th{color:#183d28;font-size:1.12rem;font-weight:950;letter-spacing:.04em;background:#f4faf5;vertical-align:top;}
    .parent-homework-table td{background:#fff;}
    .parent-homework-diary-board{display:flex;flex-direction:column;gap:.8rem;}
    .parent-work-row{display:grid;grid-template-columns:7.5rem minmax(0,1fr);gap:.85rem;align-items:stretch;border-radius:22px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:.85rem;box-shadow:0 10px 24px rgba(24,76,46,.05);}
    .parent-work-label{display:flex;align-items:center;justify-content:center;min-height:3.25rem;border-radius:16px;background:#eef8f0;color:#244c32;font-weight:950;letter-spacing:.04em;text-align:center;}
    .parent-work-content{min-width:0;display:flex;flex-direction:column;gap:.75rem;}
    .parent-work-row--diary{background:linear-gradient(135deg,#ffffff 0%,#fbfff8 100%);}
    .parent-work-row--sign{background:linear-gradient(135deg,#fffdf8 0%,#f8fff9 100%);}
    .parent-homework-card{border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .parent-homework-title{font-size:1.08rem;font-weight:950;color:#17231b;line-height:1.35;}
    .parent-homework-desc{color:#46524a;font-size:.98rem;line-height:1.7;margin-top:.35rem;}
    .parent-homework-meta{color:#647067;font-size:.88rem;font-weight:800;margin-top:.55rem;}
    .parent-empty--soft{border-radius:16px;background:#f8fbf6;border:1px dashed rgba(35,92,59,.16);padding:1rem;}
    .parent-date-pill{display:inline-flex;align-items:center;justify-content:center;border-radius:999px;background:#edf8f0;border:1px solid rgba(35,92,59,.1);color:#2f7446;font-weight:850;font-size:.82rem;padding:.35rem .7rem;white-space:nowrap;}
    .parent-empty{color:#718078;font-size:.94rem;padding:.35rem 0;}
    .parent-empty--panel{border-radius:20px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;}
    .parent-date-nav{display:flex;gap:.55rem;align-items:center;flex-wrap:wrap;justify-content:flex-end;}
    .parent-diary-date-nav{align-self:center;}
    .parent-date-form{display:flex;gap:.45rem;align-items:center;}
    .parent-date-form .form-control{border-radius:999px;min-width:10rem;}
	    .parent-service-list{display:flex;flex-direction:column;gap:.75rem;}
	    .parent-service-row{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;}
	    .parent-service-row strong{display:block;color:#1a2a20;font-weight:900;}
	    .parent-service-row span{display:block;color:#647067;font-size:.92rem;margin-top:.15rem;}
	    .parent-service-actions{display:flex;gap:.5rem;flex-wrap:wrap;justify-content:flex-end;}
	    .parent-journal-list{display:flex;flex-direction:column;gap:.9rem;}
    .parent-diary-item{border-radius:22px;border:1px solid rgba(19,56,35,.08);background:linear-gradient(180deg,#fff 0%,#fbfff8 100%);padding:1rem;box-shadow:0 14px 30px rgba(24,76,46,.07);}
    .parent-diary-inline{display:flex;flex-direction:column;gap:.75rem;}
    .parent-diary-inline-head{display:flex;justify-content:space-between;align-items:flex-start;gap:.75rem;flex-wrap:wrap;padding:.1rem .1rem 0;}
    .parent-diary-top{display:flex;justify-content:space-between;gap:.8rem;align-items:flex-start;flex-wrap:wrap;margin-bottom:.85rem;}
    .parent-child-name{font-size:1.14rem;font-weight:900;color:#1a2a20;}
    .parent-badge-stack{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:flex-end;}
    .parent-diary-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(16rem,.85fr);gap:.8rem;align-items:stretch;}
    .journal-panel{background:#fff;border-radius:18px;padding:1rem;border:1px solid rgba(15,23,42,.08);height:100%;box-shadow:inset 0 0 0 1px rgba(255,255,255,.65);}
    .journal-panel--diary{background:#fbfdf8;}
    .journal-panel--sign{background:#fffdf8;}
    .parent-sign-card{display:flex;flex-direction:column;align-items:flex-start;}
    .parent-sign-head{width:100%;display:flex;align-items:center;justify-content:space-between;gap:.75rem;flex-wrap:wrap;margin-bottom:.55rem;}
    .journal-panel-title{font-size:1rem;font-weight:900;letter-spacing:.08em;text-transform:uppercase;color:#5d8f68;margin-bottom:.45rem;}
    .journal-heading{font-size:1.24rem;font-weight:850;color:#1d2a22;margin-bottom:.75rem;}
    .journal-panel-body{font-size:1.1rem;color:#1f2937;line-height:1.76;}
    .journal-panel-body .border{border-color:rgba(19,56,35,.12)!important;border-radius:14px!important;}
    .journal-prompt-card,.journal-entry-card,.journal-locked-card{border-radius:18px;border:1px solid rgba(19,56,35,.08);padding:1rem;background:#fff;margin-bottom:.8rem;}
    .journal-prompt-card{background:linear-gradient(135deg,#f8fff9 0%,#fffdf7 100%);}
    .journal-prompt-note{color:#5d6d61;font-size:1.12rem;line-height:1.78;margin-bottom:.75rem;}
    .journal-prompt-note--empty{color:#8a968d;border:1px dashed rgba(19,56,35,.12);border-radius:14px;padding:.75rem;background:#f8fafc;}
    .journal-entry-card{background:linear-gradient(180deg,#fff 0%,#fffdf7 100%);}
    .journal-entry-text{border-radius:16px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem 1.1rem;font-size:1.18rem;line-height:1.95;color:#24362c;}
    .journal-locked-card{background:#f8fafc;border-style:dashed;color:#647067;}
    .journal-locked-card strong{display:block;color:#1d2a22;font-weight:900;margin-bottom:.25rem;}
    .journal-locked-card span{display:block;color:#647067;font-size:1.02rem;line-height:1.65;}
    .parent-journal-card img.journal-img,.journal-panel img.journal-img{border-radius:14px;max-width:100%;height:auto;box-shadow:0 12px 28px rgba(15,23,42,.15);}
    .parent-kids-grid{display:flex;flex-direction:column;gap:1rem;}
    .parent-child-card{border:1px solid rgba(19,56,35,.08)!important;border-radius:24px!important;background:linear-gradient(180deg,#fff 0%,#fbfff8 100%);box-shadow:0 16px 38px rgba(24,76,46,.08)!important;overflow:hidden;}
    .parent-child-card__body{padding:1.15rem;display:flex;flex-direction:column;gap:1rem;}
    .parent-child-card .accordion{position:relative;z-index:2;}
    .parent-child-card .accordion-item{border:1px solid rgba(19,56,35,.08);border-radius:16px!important;overflow:hidden;margin-top:.45rem;}
    .parent-child-card .accordion-button{font-weight:800;color:#203226;background:#fbfdf8;}
    .parent-reading-dashboard-head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;}
    .parent-reading-summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;}
    .parent-reading-overview-grid{display:grid;grid-template-columns:1fr;gap:1rem;}
    .parent-reading-section{border-radius:22px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:1rem;box-shadow:0 10px 24px rgba(24,76,46,.05);}
    .parent-reading-section-title{display:flex;justify-content:space-between;align-items:center;gap:.75rem;flex-wrap:wrap;font-weight:900;color:#183d28;margin-bottom:.8rem;}
    .parent-reading-record-list{display:flex;flex-direction:column;gap:.65rem;}
    .parent-reading-record{display:flex;justify-content:space-between;align-items:center;gap:.9rem;border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:.85rem .95rem;}
    .parent-reading-record-main{min-width:0;}
    .parent-reading-record-title{font-weight:900;color:#17231b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .parent-reading-record-meta{font-size:.86rem;color:#647067;margin-top:.15rem;}
    .parent-reading-record-side{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:.25rem;flex-shrink:0;}
    .parent-reading-record-score{font-size:.86rem;color:#647067;font-weight:850;}
    .parent-reading-tag{display:inline-flex;margin-left:.3rem;border-radius:999px;font-size:.75rem;font-weight:850;padding:.12rem .45rem;background:#e5f4ea;color:#2f7447;}
    .parent-reading-tag--todo{background:#fff3cd;color:#8a5b12;}
    .parent-reading-progress{position:relative;z-index:2;border-radius:18px;background:#f8fbf6;border:1px solid rgba(35,92,59,.08);padding:1rem;margin-top:1rem;}
    .parent-reading-progress-title{font-weight:900;color:#183d28;margin-bottom:.55rem;}
    .parent-coread-panel{position:relative;z-index:2;border-radius:20px;background:#f8fbf6;border:1px solid rgba(35,92,59,.08);padding:1rem;margin-top:0;}
    .parent-coread-panel--urgent{background:linear-gradient(135deg,#fff8e7 0%,#f5fff7 100%);border-color:rgba(209,142,36,.22);}
    .parent-coread-head{display:flex;justify-content:space-between;align-items:flex-start;gap:.75rem;flex-wrap:wrap;margin-bottom:.7rem;}
    .parent-coread-list{display:flex;flex-direction:column;gap:.6rem;}
    .parent-coread-item{display:flex;justify-content:space-between;align-items:center;gap:.9rem;flex-wrap:wrap;background:#fff;border:1px solid rgba(19,56,35,.08);border-radius:18px;padding:.95rem;box-shadow:0 10px 22px rgba(24,76,46,.05);}
    .parent-coread-item>div{min-width:0;}
    .parent-coread-title{font-size:1.03rem;font-weight:950;color:#17231b;line-height:1.3;}
    .parent-coread-meta{color:#647067;font-size:.9rem;font-weight:800;margin-top:.2rem;}
    .parent-coread-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.55rem;margin-bottom:.75rem;}
    .parent-coread-summary-item{border-radius:14px;background:#fff;border:1px solid rgba(19,56,35,.07);padding:.6rem .7rem;}
    .parent-coread-summary-item span{display:block;color:#718078;font-size:.76rem;font-weight:900;}
    .parent-coread-summary-item strong{display:block;color:#183d28;font-size:1.05rem;font-weight:900;line-height:1.1;margin-top:.12rem;}
    .parent-coread-progress{height:.6rem;border-radius:999px;background:#e8f2ea;overflow:hidden;margin-bottom:.75rem;}
    .parent-coread-progress span{display:block;height:100%;border-radius:999px;background:#34784a;}
    .parent-progress-line{display:grid;grid-template-columns:6rem minmax(0,1fr) 7.5rem;gap:.75rem;align-items:center;margin:.7rem 0;}
    .parent-progress-line .progress{height:.7rem;border-radius:999px;background:#e8f2ea;}
    .parent-progress-line .progress-bar{border-radius:999px;background:#34784a;}
    .parent-progress-name{font-weight:900;color:#244c32;}
    .parent-progress-number{font-size:.86rem;color:#647067;text-align:right;font-weight:800;}
    .ui-kpi{background:#f6faf7;border-radius:16px;padding:.9rem 1rem;height:100%;box-shadow:inset 0 0 0 1px rgba(35,92,59,.07);}
    .ui-kpi .kpi-label{font-size:.76rem;font-weight:900;letter-spacing:.06em;color:#78917e;margin-bottom:.18rem;}
	    .ui-kpi .kpi-value{font-size:1.35rem;font-weight:900;color:#183d28;}
	    .ui-kpi .kpi-sub{font-size:.8rem;color:#66766b;margin-top:.12rem;}
	    @media (max-width:1199.98px){
	      .parent-metric-grid,.parent-reading-summary-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
	    }
	    @media (max-width:767.98px){
	      .parent-hero,.parent-panel{border-radius:22px;}
	      .parent-metric-grid,.parent-work-row,.parent-diary-grid,.parent-reading-summary-grid,.parent-coread-summary{grid-template-columns:1fr;}
	      .parent-work-label{justify-content:flex-start;min-height:auto;padding:.65rem .8rem;}
	      .parent-progress-line{grid-template-columns:1fr;gap:.35rem;}
	      .parent-progress-number{text-align:left;}
      .parent-reading-record{align-items:flex-start;flex-direction:column;}
      .parent-reading-record-side{text-align:left;align-items:flex-start;}
	      .parent-hero-actions{justify-content:flex-start;}
	      .parent-service-actions{justify-content:flex-start;width:100%;}
	      .parent-panel-head,.parent-panel-body{padding:1rem;}
	      .parent-date-nav{justify-content:flex-start;}
	      .parent-date-form{width:100%;}
	      .parent-date-form .form-control{flex:1;min-width:0;}
	    }
    </style>
    """

    coread_pending_total = 0
    if not kids:
        kids_block = """
        <section class="parent-panel">
          <div class="parent-panel-body">
            <div class="alert alert-warning mb-0">
              目前尚未綁定任何學童。請先完成綁定後，即可查看孩子的閱讀與聯絡簿資訊。
            </div>
          </div>
        </section>
        """
    else:
        cards = []

        for idx, stu in enumerate(kids, start=1):
            stu_name = stu.display_name or stu.username
            stu_info = f"{stu.unit or ''} · {stu.grade or ''}年{stu.class_no or ''}班"

            
            week_pts, _ = _user_week_points(stu.id, ws, we)
            streak = _streak_weeks(stu.id, today_d)

            
            q_all = (
                CompletedTask.query
                .join(Task, CompletedTask.task_id == Task.id)
                .filter(Task.task_type == "mission")
            )
            q_all = ct_user_filter(q_all, stu.id)

            col = ct_time_col()
            q_all = q_all.order_by(col.desc()) if col is not None else q_all.order_by(CompletedTask.id.desc())
            rows = q_all.limit(120).all()

            total_pts = 0
            month_pts = 0
            pending_cnt = approved_cnt = rejected_cnt = 0
            coread_todo = 0
            sdg_scores = {code: 0 for code, _ in SDG_OPTIONS}

            for ct in rows:
                p = _ct_load(ct) or {}
                st = _ct_status(ct)

                if st == "approved":
                    approved_cnt += 1
                    score = _safe_int(p.get("score_total", 0), 0)
                    total_pts += score
                    try:
                        ct_day = _ct_get_timestamp(ct).date()
                    except Exception:
                        ct_day = None
                    if ct_day and month_start <= ct_day <= month_end:
                        month_pts += score
                        raw_codes = p.get("sdg_codes")
                        if isinstance(raw_codes, (list, tuple)):
                            codes = raw_codes
                        elif raw_codes:
                            codes = [raw_codes]
                        elif p.get("sdg_code"):
                            codes = [p.get("sdg_code")]
                        else:
                            codes = []
                        used_codes = set()
                        for raw_code in codes:
                            code = _safe_int(raw_code, 0)
                            if code not in sdg_scores or code in used_codes:
                                continue
                            used_codes.add(code)
                            sdg_scores[code] += score
                elif st == "pending":
                    pending_cnt += 1
                else:
                    rejected_cnt += 1

                if need_parent_coread(ct, p) and (not parent_coread_done(p)) and st != "rejected":
                    coread_todo += 1

            level = _level_of(total_pts)
            term_pts = total_pts
            week_pct = pct(week_pts, week_goal)
            month_pct = pct(month_pts, month_goal)
            term_pct = pct(term_pts, term_goal)
            week_label = f"{week_pts} / {week_goal} 分" if week_goal > 0 else f"{week_pts} 分"
            month_label = f"{month_pts} / {month_goal} 分" if month_goal > 0 else f"{month_pts} 分"
            term_label = f"{term_pts} / {term_goal} 分" if term_goal > 0 else f"{term_pts} 分"
            reading_progress_html = f"""
                <div class="parent-reading-progress">
                  <div class="parent-reading-progress-title">閱讀進度</div>
                  <div class="parent-progress-line">
                    <div class="parent-progress-name">本週</div>
                    <div class="progress"><div class="progress-bar" style="width:{week_pct}%;"></div></div>
                    <div class="parent-progress-number">{week_label}</div>
                  </div>
                  <div class="parent-progress-line">
                    <div class="parent-progress-name">本月</div>
                    <div class="progress"><div class="progress-bar" style="width:{month_pct}%;"></div></div>
                    <div class="parent-progress-number">{month_label}</div>
                  </div>
                  <div class="parent-progress-line">
                    <div class="parent-progress-name">本學期</div>
                    <div class="progress"><div class="progress-bar" style="width:{term_pct}%;"></div></div>
                    <div class="parent-progress-number">{term_label}</div>
                  </div>
                </div>
            """

            coread_todo_cards = []
            coread_done_cards = []
            coread_total_items = 0
            coread_done_count = 0
            coread_todo_count = 0
            coread_locked_count = 0
            for ct in rows[:60]:
                p = _ct_load(ct) or {}
                st = _ct_status(ct)

                if not need_parent_coread(ct, p):
                    continue
                coread_total_items += 1
                if st == "rejected":
                    coread_locked_count += 1
                    continue

                book_title = (p.get("book_title") or "未填書名").strip()
                try:
                    ts_txt = _ct_get_timestamp(ct).strftime("%m/%d %H:%M")
                except Exception:
                    ts_txt = ""

                done = parent_coread_done(p)
                if done:
                    coread_done_count += 1
                    badge = "<span class='badge bg-success ms-1'>已完成</span>"
                    btn = f"<a class='btn parent-soft-btn' href='{safe_url_for('reading_parent_coread', ct_id=ct.id)}'>查看回饋</a>"
                else:
                    coread_todo_count += 1
                    badge = "<span class='badge bg-warning text-dark ms-1'>待補寫</span>"
                    btn = f"<a class='btn parent-soft-btn parent-soft-btn--green' href='{safe_url_for('reading_parent_coread', ct_id=ct.id)}'>前往補寫</a>"

                task_name = escape(getattr(ct.task, "title", "") or p.get("reading_task_title", "") or "永續閱讀任務")
                card_html = (
                    "<article class='parent-coread-item'>"
                    "<div>"
                    f"<div class='parent-coread-title'>《{escape(book_title)}》{badge}</div>"
                    f"<div class='parent-coread-meta'>{task_name} · {ts_txt}</div>"
                    "</div>"
                    f"{btn}"
                    "</article>"
                )
                if done:
                    if len(coread_done_cards) < 2:
                        coread_done_cards.append(card_html)
                else:
                    if len(coread_todo_cards) < 3:
                        coread_todo_cards.append(card_html)

            coread_done_pct = pct(coread_done_count, coread_total_items)
            coread_summary = (
                "<div class='parent-coread-summary'>"
                f"<div class='parent-coread-summary-item'><span>待補寫</span><strong>{coread_todo_count}</strong></div>"
                f"<div class='parent-coread-summary-item'><span>已完成</span><strong>{coread_done_count}</strong></div>"
                f"<div class='parent-coread-summary-item'><span>需孩子修正</span><strong>{coread_locked_count}</strong></div>"
                "</div>"
                f"<div class='parent-coread-progress'><span style='width:{coread_done_pct}%;'></span></div>"
            )
            if coread_todo_cards:
                coread_panel = (
                    "<div class='parent-coread-panel parent-coread-panel--urgent'>"
                    "<div class='parent-coread-head'>"
                    "<div><div class='parent-section-kicker'>Co-reading</div>"
                    "<div class='parent-reading-progress-title'>親子共讀待辦</div></div>"
                    f"<span class='badge bg-warning text-dark'>待補寫 {coread_todo_count}</span>"
                    "</div>"
                    f"{coread_summary}"
                    "<div class='parent-muted mb-2'>完成家長共讀回饋後，這筆閱讀會送到老師端審核。</div>"
                    "<div class='parent-coread-list'>"
                    + "".join(coread_todo_cards)
                    + "</div></div>"
                )
            elif coread_done_cards:
                coread_panel = (
                    "<div class='parent-coread-panel'>"
                    "<div class='parent-coread-head'>"
                    "<div><div class='parent-section-kicker'>Co-reading</div>"
                    "<div class='parent-reading-progress-title'>親子共讀已完成</div></div>"
                    "<span class='badge bg-success'>都已處理</span>"
                    "</div>"
                    f"{coread_summary}"
                    "<div class='parent-muted mb-2'>可查看或補充已儲存的親子共讀回饋。</div>"
                    "<div class='parent-coread-list'>"
                    + "".join(coread_done_cards[:2])
                    + "</div></div>"
                )
            else:
                coread_panel = ""

	            
            sdg_name_map = {code: name for code, name in SDG_OPTIONS}
            sdg_chart_html = _reading_sdg_chart_html(
                sdg_scores,
                sdg_name_map,
                "孩子本月尚未有通過的閱讀認證，完成後會產生長條圖與圓餅圖。",
            )

            recent_cards = []
            for ct in rows[:5]:
                p = _ct_load(ct) or {}
                st = _ct_status(ct)
                book_title = (p.get("book_title") or "未填書名").strip()
                try:
                    ts_txt = _ct_get_timestamp(ct).strftime("%Y-%m-%d")
                except Exception:
                    ts_txt = "未記錄日期"
                score_txt = f"{_safe_int(p.get('score_total', 0), 0)} 分" if st == "approved" else ("未計分" if st == "rejected" else "審核中")
                raw_codes = p.get("sdg_codes")
                if isinstance(raw_codes, (list, tuple)):
                    codes = raw_codes
                elif raw_codes:
                    codes = [raw_codes]
                elif p.get("sdg_code"):
                    codes = [p.get("sdg_code")]
                else:
                    codes = []
                sdg_txt = "、".join([f"{_safe_int(c, 0):02d}" for c in codes if _safe_int(c, 0) > 0]) or "未分類"
                coread_tag = ""
                if need_parent_coread(ct, p):
                    coread_tag = (
                        "<span class='parent-reading-tag'>親子共讀已完成</span>"
                        if parent_coread_done(p) else
                        "<span class='parent-reading-tag parent-reading-tag--todo'>待補親子共讀</span>"
                    )
                recent_cards.append(
                    "<article class='parent-reading-record'>"
                    "<div class='parent-reading-record-main'>"
                    f"<div class='parent-reading-record-title'>《{escape(book_title)}》</div>"
                    f"<div class='parent-reading-record-meta'>{ts_txt} · SDG {escape(sdg_txt)} {coread_tag}</div>"
                    "</div>"
                    "<div class='parent-reading-record-side'>"
                    f"{status_badge(st)}"
                    f"<div class='parent-reading-record-score'>{score_txt}</div>"
                    "</div>"
                    "</article>"
                )
            history_html = "".join(recent_cards) or "<div class='parent-empty parent-empty--panel'>目前尚無閱讀認證紀錄。</div>"

            cards.append(f"""
            <article class="parent-child-card parent-reading-dashboard">
              <div class="parent-child-card__body">
                <div class="parent-reading-dashboard-head">
                  <div>
                    <div class="parent-section-kicker">Reading Overview</div>
                    <div class="parent-child-name">{escape(stu_name)}</div>
                    <div class="parent-muted">{escape(stu_info)} · 本週 {week_range_txt}</div>
                  </div>

                  <div class="text-end">
                    <span class="badge bg-light text-muted border">親子共讀待補寫：{coread_todo}</span>
                  </div>
                </div>

                <div class="parent-reading-summary-grid">
                  {kpi_tile("本週分數", str(week_pts))}
                  {kpi_tile("等級", str(level), f"歷史：{total_pts} 分")}
                  {kpi_tile("連續週數", str(streak), "每週持續閱讀累積")}
                  {kpi_tile("待審核", str(pending_cnt), f"通過 {approved_cnt} / 退回 {rejected_cnt}")}
                </div>
                {reading_progress_html}

                <div class="parent-reading-overview-grid">
                  <section class="parent-reading-section">
                    <div class="parent-reading-section-title">
                      <span>本月 SDG 圖表</span>
                      <span class="parent-muted">圓餅圖與長條圖</span>
                    </div>
                    {sdg_chart_html}
                  </section>
                  <section class="parent-reading-section">
                    <div class="parent-reading-section-title">
                      <span>最近閱讀紀錄</span>
                      <span class="parent-muted">最新 5 筆</span>
                    </div>
                    <div class="parent-reading-record-list">{history_html}</div>
                  </section>
                  {coread_panel}
                </div>
              </div>
            </article>
            """)

            coread_pending_total += coread_todo

        kids_block = (
            "<section class='parent-panel parent-kids-section' id='parentReadingOverview'>"
            "<div class='parent-panel-head'>"
            "<div>"
            "<div class='parent-section-kicker'>Children</div>"
            "<h4>孩子閱讀概況</h4>"
            "</div>"
            f"<span class='badge bg-light text-muted border align-self-center'>共 {len(kids)} 位</span>"
            "</div>"
            f"<div class='parent-panel-body'><div class='parent-kids-grid'>{''.join(cards)}</div></div>"
            "</section>"
        )

    kids_total = len(kids)
    today_txt = today_d.strftime("%Y/%m/%d")

    def hero_metric(label, value, sub=""):
        extra = f"<div class='hero-metric-sub'>{sub}</div>" if sub else ""
        return (
            "<div class='hero-metric'>"
            f"<div class='hero-metric-value'>{value}</div>"
            f"<div class='hero-metric-label'>{label}</div>"
            f"{extra}"
            "</div>"
        )

    hero_block = f"""
    <section class='parent-hero'>
      <div class='parent-hero-inner'>
        <div>
          <div class='parent-eyebrow'>家長總覽</div>
          <h3>您好，{parent_name}</h3>
          <div class='parent-muted mt-1'>今天 {today_txt} · 已綁定 {kids_total} 位學童</div>
        </div>
        <div class='parent-hero-actions'>
          <a class='btn parent-soft-btn' href='/profile'>個人檔案</a>
        </div>
      </div>
      <div class='parent-metric-grid'>
        {hero_metric("待簽名", need_sign_total, f"{selected_word}尚未完成")}
        {hero_metric("公開日記", shared_diary_total, "可查看篇數")}
        {hero_metric("親子共讀", coread_pending_total, "待補寫")}
        {hero_metric("綁定學童", kids_total)}
      </div>
    </section>
    """

    child_usernames = [stu.username for stu in kids]
    leave_rows = []
    leave_pending = 0
    if child_usernames:
        try:
            leave_pending = (
                LeaveRequest.query
                .filter(LeaveRequest.student_name.in_(child_usernames), LeaveRequest.status.is_(None))
                .count()
            )
            leave_rows = (
                LeaveRequest.query
                .filter(LeaveRequest.student_name.in_(child_usernames))
                .order_by(LeaveRequest.created_at.desc())
                .limit(3)
                .all()
            )
        except Exception:
            leave_rows = []
            leave_pending = 0

    def leave_status_text(v):
        if v is None:
            return "<span class='badge bg-warning text-dark'>待審</span>"
        if v == 1:
            return "<span class='badge bg-success'>已核准</span>"
        return "<span class='badge bg-danger'>已駁回</span>"

    leave_recent_html = (
        "".join(
            "<div class='parent-service-row'>"
            f"<div><strong>{escape(r.student_display or r.student_name)}</strong>"
            f"<span>{escape(str(r.start_date))} ~ {escape(str(r.end_date))} · {escape(r.leave_type or '')}</span></div>"
            f"<div>{leave_status_text(r.status)}</div>"
            "</div>"
            for r in leave_rows
        )
        if leave_rows
        else "<div class='parent-service-row'><div><strong>尚無請假紀錄</strong><span>需要請假時可直接送出申請。</span></div></div>"
    )

    service_block = f"""
    <section class='parent-panel parent-service-panel'>
      <div class='parent-panel-head'>
        <div>
          <div class='parent-section-kicker'>Service</div>
          <h4>請假與日常服務</h4>
        </div>
        <span class='badge bg-light text-muted border align-self-center'>待審請假 {leave_pending}</span>
      </div>
      <div class='parent-panel-body'>
        <div class='parent-service-row mb-3'>
          <div>
            <strong>請假申請</strong>
            <span>送出孩子的事假、病假或其他請假需求，並查看老師審核狀態。</span>
          </div>
          <div class='parent-service-actions'>
            <a class='btn parent-soft-btn parent-soft-btn--green' href='/leave'>新增請假</a>
            <a class='btn parent-soft-btn' href='/leave/mine'>我的請假</a>
          </div>
        </div>
        <div class='parent-service-row mb-3'>
          <div>
            <strong>用藥紀錄與親師交流</strong>
            <span>需要交代用藥或與老師溝通時，可從這裡進入。</span>
          </div>
          <div class='parent-service-actions'>
            <a class='btn parent-soft-btn' href='/medication'>用藥紀錄</a>
            <a class='btn parent-soft-btn' href='{comm_url}'>親師交流</a>
          </div>
        </div>
        <div class='parent-service-list'>{leave_recent_html}</div>
      </div>
    </section>
    """

    content = (
	        page_style
	        + "<div class='parent-page'>"
	        + hero_block
	        + service_block
	        + "<div class='parent-main-grid'>"
	        + contactbook_block
	        + "</div>"
        + kids_block
        + "</div>"
    )
    return page("家長首頁", content)

@app.route("/reading/dashboard_child/<student_username>", endpoint="reading_dashboard_child")
@roles_required("parent")
def reading_dashboard_child(student_username):
    """
    家長查看某一位孩子的閱讀儀表板（UIUX 優化版）
    - 僅限有綁定該學童的家長
    - 分頁：總覽 / 親子共讀 / 閱讀歷程
    - 規則：只有「該筆認證有勾選親子共讀」才顯示家長可填寫入口
    """
    from sqlalchemy import func

    
    
    
    stu = User.query.filter_by(username=student_username, role="student").first()
    if not stu:
        return toast_redirect("parent", "找不到這位學童，請確認帳號或洽詢老師。", "warning")

    bound = parent_child_query(student=stu.username).first()
    if not bound:
        return toast_redirect("parent", "此學童未與你綁定，無法查看閱讀儀表板。", "warning")

    
    
    
    def safe_url_for(endpoint, **values):
        try:
            return url_for(endpoint, **values)
        except Exception:
            if endpoint == "reading_parent_coread" and "ct_id" in values:
                return f"/reading/parent_coread/{values['ct_id']}"
            return "#"

    def need_parent_coread(ct, payload: dict) -> bool:
        return reading_need_parent_coread(ct=ct, payload=payload, task=getattr(ct, "task", None))

    def parent_coread_done(payload: dict) -> bool:
        return reading_parent_coread_done(payload)

    def status_badge(st: str) -> str:
        if st == "approved":
            return "<span class='badge text-bg-success'>已通過</span>"
        if st == "pending":
            return "<span class='badge text-bg-warning text-dark'>待審核</span>"
        return "<span class='badge text-bg-danger'>已退回</span>"

    
    
    
    today_d = _today()
    ws, we = _week_range(today_d)
    week_range_txt = f"{ws.strftime('%m/%d')} - {we.strftime('%m/%d')}"

    week_pts, week_sdg_set = _user_week_points(stu.id, ws, we)
    streak = _streak_weeks(stu.id, today_d)
    delta, nxt, _curw = _next_badge_delta(stu.id, today_d)

    
    week_goal = globals().get("READING_WEEK_GOAL", 30)
    month_goal = globals().get("READING_MONTH_GOAL", 120)
    term_goal = globals().get("READING_TERM_GOAL", 300)

    def goal_int(v, default):
        return int(v) if isinstance(v, (int, float)) else default

    week_goal = goal_int(week_goal, 30)
    month_goal = goal_int(month_goal, 120)
    term_goal = goal_int(term_goal, 300)

    def pct(n, d):
        try:
            if d <= 0:
                return 0
            return int(max(0, min(100, round((n / d) * 100))))
        except Exception:
            return 0

    week_goal_pct = pct(week_pts, week_goal)
    badge_pct = pct(week_pts, nxt)

    
    
    
    q_all = (
        CompletedTask.query
        .join(Task, CompletedTask.task_id == Task.id)
        .filter(Task.task_type == "mission")
    )
    q_all = ct_user_filter(q_all, stu.id)

    col = ct_time_col()
    q_all = q_all.order_by(col.desc()) if col is not None else q_all.order_by(CompletedTask.id.desc())

    raw_rows = q_all.limit(160).all()  

    rows = []
    for ct in raw_rows:
        try:
            p = _ct_load(ct) or {}
        except Exception:
            p = {}
        try:
            st = _ct_status(ct)
        except Exception:
            st = "pending"
        try:
            ts = _ct_get_timestamp(ct)
        except Exception:
            ts = None

        rows.append({
            "ct": ct,
            "payload": p,
            "status": st,
            "ts": ts,
        })

    
    
    
    SDG17 = {
        1: "無貧窮", 2: "零飢餓", 3: "健康與福祉", 4: "優質教育", 5: "性別平等",
        6: "淨水與衛生", 7: "可負擔及潔淨能源", 8: "合適工作與經濟成長",
        9: "產業創新與基礎建設", 10: "減少不平等", 11: "永續城鄉",
        12: "責任消費與生產", 13: "氣候行動", 14: "海洋生態",
        15: "陸域生態", 16: "和平正義與健全制度", 17: "夥伴關係",
    }

    m_first, m_last = _month_range(today_d)
    total_pts = 0
    month_pts = 0
    approved_cnt = pending_cnt = rejected_cnt = 0
    last_approved_ts = None
    sdg_scores = {code: 0 for code in SDG17}

    for r in rows:
        st = r["status"]
        p = r["payload"]

        if st == "approved":
            approved_cnt += 1
            score = _safe_int(p.get("score_total", 0), 0)
            total_pts += score
            if r["ts"] is not None and (last_approved_ts is None or r["ts"] > last_approved_ts):
                last_approved_ts = r["ts"]
            r_date = r["ts"].date() if r["ts"] else None
            if r_date and m_first <= r_date <= m_last:
                month_pts += score
                raw_codes = p.get("sdg_codes")
                if isinstance(raw_codes, (list, tuple)):
                    codes = raw_codes
                elif raw_codes:
                    codes = [raw_codes]
                elif p.get("sdg_code"):
                    codes = [p.get("sdg_code")]
                else:
                    codes = []
                used_codes = set()
                for raw_code in codes:
                    code = _safe_int(raw_code, 0)
                    if code not in sdg_scores or code in used_codes:
                        continue
                    used_codes.add(code)
                    sdg_scores[code] += score
        elif st == "pending":
            pending_cnt += 1
        else:
            rejected_cnt += 1

    level = _level_of(total_pts)
    last_txt = last_approved_ts.strftime("%Y-%m-%d %H:%M") if last_approved_ts else "—"
    month_goal_pct = pct(month_pts, month_goal)
    term_goal_pct = pct(total_pts, term_goal)

    sdg_list = []
    for c in sorted(list(week_sdg_set or [])):
        try:
            c_int = int(c)
        except Exception:
            continue
        if c_int in SDG17:
            sdg_list.append(c_int)

    if sdg_list:
        sdg_badges = "".join(
            f"<span class='badge text-bg-light border me-1 mb-1'>SDG {c:02d} · {SDG17[c]}</span>"
            for c in sdg_list[:12]
        )
    else:
        sdg_badges = "<span class='text-muted small'>本週尚未累積到已核准的閱讀紀錄。</span>"

    
    
    
    todo_items = []
    done_items = []
    coread_total = 0
    blocked_coread = 0
    for r in rows[:80]:
        ct = r["ct"]
        p = r["payload"]
        st = r["status"]

        if not need_parent_coread(ct, p):
            continue
        coread_total += 1
        if st == "rejected":
            blocked_coread += 1
            continue

        book_title = (p.get("book_title") or "未填書名").strip()
        score_preview = _safe_int(p.get("score_total", 0), 0)
        ts_txt = r["ts"].strftime("%m/%d %H:%M") if r["ts"] else "—"
        task_name = getattr(ct.task, "title", "") or p.get("reading_task_title", "") or "永續閱讀任務"

        if parent_coread_done(p):
            done_items.append((ct.id, ts_txt, book_title, score_preview, task_name))
        else:
            todo_items.append((ct.id, ts_txt, book_title, score_preview, task_name))

    if todo_items:
        todo_html = ""
        for ct_id, ts_txt, book_title, score_preview, task_name in todo_items[:8]:
            todo_html += f"""
            <article class="kid-coread-card kid-coread-card--todo">
              <div>
                <div class="kid-coread-title">《{escape(book_title)}》</div>
                <div class="kid-coread-muted">{escape(task_name)} · {ts_txt}</div>
                <div class="kid-coread-muted">老師會在家長回饋完成後審核。</div>
              </div>
              <div class="kid-coread-action">
                <span class="badge bg-warning text-dark mb-2">待補寫</span>
                <a class="btn kid-soft-btn kid-soft-btn--green" href="{safe_url_for('reading_parent_coread', ct_id=ct_id)}">去填寫</a>
              </div>
            </article>
            """
    else:
        todo_html = "<div class='kid-coread-empty'>目前沒有需要補寫的親子共讀。</div>"

    done_html = ""
    for ct_id, ts_txt, book_title, score_preview, task_name in done_items[:4]:
        done_html += f"""
        <article class="kid-coread-card">
          <div>
            <div class="kid-coread-title">《{escape(book_title)}》</div>
            <div class="kid-coread-muted">{escape(task_name)} · {ts_txt}</div>
          </div>
          <div class="kid-coread-action">
            <span class="badge bg-success mb-2">已完成</span>
            <a class="btn kid-soft-btn" href="{safe_url_for('reading_parent_coread', ct_id=ct_id)}">查看/修改</a>
          </div>
        </article>
        """
    if not done_html:
        done_html = "<div class='kid-coread-empty'>尚無已完成的親子共讀紀錄。</div>"

    coread_done_pct = pct(len(done_items), coread_total)
    active_sdg_pairs = [(code, score) for code, score in sdg_scores.items() if score > 0]
    max_sdg_score = max(sdg_scores.values()) if any(sdg_scores.values()) else 0
    if active_sdg_pairs:
        top_sdg_code, top_sdg_score = max(active_sdg_pairs, key=lambda item: item[1])
        zero_sdg_codes = [code for code, score in sdg_scores.items() if score == 0]
        recommend_sdg_code = zero_sdg_codes[0] if zero_sdg_codes else min(sdg_scores.items(), key=lambda item: item[1])[0]
        top_sdg_text = f"SDG {top_sdg_code:02d} · {SDG17[top_sdg_code]}"
        focus_text = f"孩子本月最常累積在 {top_sdg_text}，可再試試 SDG {recommend_sdg_code:02d} · {SDG17[recommend_sdg_code]}。"
        sdg_bars_html = "".join(
            "<div class='kid-sdg-row'>"
            f"<div class='kid-sdg-name'>SDG {code:02d} · {escape(SDG17[code])}</div>"
            "<div class='kid-sdg-track'>"
            f"<div class='kid-sdg-fill' style='width:{max(8, pct(score, max_sdg_score))}%;'></div>"
            "</div>"
            f"<div class='kid-sdg-score'>{score} 分</div>"
            "</div>"
            for code, score in sorted(active_sdg_pairs, key=lambda item: item[1], reverse=True)[:6]
        )
    else:
        top_sdg_text = "尚未累積"
        focus_text = "孩子本月還沒有通過的閱讀認證，完成後會出現 SDG 閱讀分布。"
        sdg_bars_html = "<div class='kid-empty'>本月尚無 SDG 分布資料。</div>"
    sdg_chart_html = _reading_sdg_chart_html(
        sdg_scores,
        SDG17,
        "孩子本月尚未有通過的閱讀認證，完成後會產生長條圖與圓餅圖。",
    )

    recent_cards = []
    for r in rows[:5]:
        ct = r["ct"]
        p = r["payload"]
        st = r["status"]
        ts_txt = r["ts"].strftime("%Y-%m-%d") if r["ts"] else "未記錄日期"
        book_title = (p.get("book_title") or "未填書名").strip()
        score_txt = f"{_safe_int(p.get('score_total', 0), 0)} 分" if st == "approved" else ("未計分" if st == "rejected" else "審核中")
        raw_codes = p.get("sdg_codes")
        if isinstance(raw_codes, (list, tuple)):
            codes = raw_codes
        elif raw_codes:
            codes = [raw_codes]
        elif p.get("sdg_code"):
            codes = [p.get("sdg_code")]
        else:
            codes = []
        sdg_txt = "、".join([f"{_safe_int(c, 0):02d}" for c in codes if _safe_int(c, 0) > 0]) or "未分類"
        coread_tag = ""
        if need_parent_coread(ct, p):
            coread_tag = (
                "<span class='kid-history-tag kid-history-tag--ok'>親子共讀已完成</span>"
                if parent_coread_done(p) else
                "<span class='kid-history-tag kid-history-tag--todo'>待補親子共讀</span>"
            )
        recent_cards.append(
            "<article class='kid-history-card'>"
            "<div class='kid-history-main'>"
            f"<div class='kid-history-title'>《{escape(book_title)}》</div>"
            f"<div class='kid-history-meta'>{ts_txt} · SDG {escape(sdg_txt)} {coread_tag}</div>"
            "</div>"
            "<div class='kid-history-side'>"
            f"{status_badge(st)}"
            f"<div class='kid-history-score'>{score_txt}</div>"
            "</div>"
            "</article>"
        )
    recent_cards_html = "".join(recent_cards) or "<div class='kid-empty'>目前尚無閱讀紀錄。</div>"

    
    
    
    history_rows = ""
    for r in rows[:40]:
        ct = r["ct"]
        p = r["payload"]
        st = r["status"]

        ts_txt = r["ts"].strftime("%Y-%m-%d %H:%M") if r["ts"] else "—"
        book_title = (p.get("book_title") or "未填書名").strip()
        score = _safe_int(p.get("score_total", 0), 0)

        
        codes = []
        if p.get("sdg_codes"):
            codes = p.get("sdg_codes") or []
        elif p.get("sdg_code"):
            codes = [p.get("sdg_code")]
        sdg_txt = "、".join([f"{_safe_int(c, 0):02d}" for c in codes if _safe_int(c, 0) > 0]) or "—"

        
        if not need_parent_coread(ct, p):
            coread_badge = "<span class='badge text-bg-light border'>未設定親子共讀</span>"
            coread_action = "<span class='text-muted small'>—</span>"
        else:
            done = parent_coread_done(p)
            if done:
                coread_badge = "<span class='badge text-bg-success'>家長已完成</span>"
                coread_action = (
                    f"<a class='btn btn-sm btn-outline-success' "
                    f"href='{safe_url_for('reading_parent_coread', ct_id=ct.id)}'>查看/修改</a>"
                )
            else:
                if st == "rejected":
                    coread_badge = "<span class='badge text-bg-secondary'>請學生先修正</span>"
                    coread_action = "<span class='text-muted small'>—</span>"
                else:
                    coread_badge = "<span class='badge text-bg-warning text-dark'>等待家長補寫</span>"
                    coread_action = (
                        f"<a class='btn btn-sm btn-primary' "
                        f"href='{safe_url_for('reading_parent_coread', ct_id=ct.id)}'>填寫</a>"
                    )

        
        stu_ref = (p.get("reflection_text") or "").strip()
        stu_ref_short = (stu_ref[:60] + "…") if len(stu_ref) > 60 else (stu_ref or "（未填寫）")

        
        reject_reason = (p.get("reject_reason") or "").strip()
        reject_html = (
            f"<div class='small text-danger mt-1'>退回原因：{escape(reject_reason)}</div>"
            if (st == "rejected" and reject_reason) else ""
        )

        
        imgs_html = ""
        for nm in (p.get("photos") or [])[:3]:
            imgs_html += img_html(str(nm), maxw=180)
        if imgs_html:
            imgs_html = f"<div class='reading-history-attachment-grid'>{imgs_html}</div>"

        score_txt = str(score) if st == "approved" else "—"

        history_rows += f"""
        <tr>
          <td class="text-nowrap small text-muted">{ts_txt}</td>
          <td>
            <div class="fw-semibold">{escape(book_title)}</div>
            <div class="small text-muted">
              狀態：{status_badge(st)}　
              SDG：{sdg_txt}　
              得分：{score_txt}
            </div>
            <details class="mt-1">
              <summary class="small text-muted" style="cursor:pointer;">學生心得：{escape(stu_ref_short)}</summary>
              <div class="small border rounded p-2 bg-light mt-1" style="white-space:pre-wrap;">{escape(stu_ref or "（未填寫）")}</div>
              {imgs_html}
              {reject_html}
            </details>
          </td>
          <td class="text-nowrap">{coread_badge}</td>
          <td class="text-nowrap text-end">{coread_action}</td>
        </tr>
        """

    if not history_rows:
        history_rows = "<tr><td colspan='4' class='text-muted small'>目前尚無閱讀認證紀錄。</td></tr>"

    
    
    
    stu_info = f"{stu.unit or ''} · {stu.grade or ''}年{stu.class_no or ''}班"
    stu_name = stu.display_name or stu.username
    parent_name = current_user.display_name or current_user.username

    badge_tip = (
        f"下一個徽章門檻：<b>{nxt}</b> 分，本週目前：<b>{week_pts}</b> 分，"
        f"還差 <b>{delta}</b> 分。"
    )

    
    overview_html = f"""
    <section class="kid-reading-hero">
      <div class="kid-reading-hero-inner">
        <div>
          <div class="kid-reading-kicker">Reading Report</div>
          <h3>{stu_name} 的閱讀概況</h3>
          <div class="kid-muted mt-1">{stu_info} · 本週 {week_range_txt}</div>
        </div>
        <div class="kid-reading-hero-note">
          <span>最近通過</span>
          <strong>{last_txt}</strong>
        </div>
      </div>
      <div class="kid-metric-grid">
        <div class="kid-metric">
          <div class="kid-metric-value">{week_pts}</div>
          <div class="kid-metric-label">本週分數</div>
          <div class="kid-metric-sub">待審核 {pending_cnt} · 退回 {rejected_cnt}</div>
        </div>
        <div class="kid-metric">
          <div class="kid-metric-value">{month_pts}</div>
          <div class="kid-metric-label">本月分數</div>
          <div class="kid-metric-sub">目標 {month_goal} 分</div>
        </div>
        <div class="kid-metric">
          <div class="kid-metric-value">{streak}</div>
          <div class="kid-metric-label">連續週數</div>
          <div class="kid-metric-sub">穩定累積閱讀習慣</div>
        </div>
        <div class="kid-metric">
          <div class="kid-metric-value">{level}</div>
          <div class="kid-metric-label">目前等級</div>
          <div class="kid-metric-sub">累積 {total_pts} 分</div>
        </div>
      </div>
    </section>

    <section class="kid-panel">
      <div class="kid-panel-head">
        <div>
          <div class="kid-reading-kicker">Progress</div>
          <h4>閱讀進度</h4>
        </div>
        <span class="kid-pill">已通過 {approved_cnt} 筆</span>
      </div>
      <div class="kid-panel-body">
        <div class="kid-progress-line">
          <div class="kid-progress-name">本週</div>
          <div class="kid-progress-track"><div class="kid-progress-fill" style="width:{week_goal_pct}%;"></div></div>
          <div class="kid-progress-number">{week_pts} / {week_goal} 分</div>
        </div>
        <div class="kid-progress-line">
          <div class="kid-progress-name">本月</div>
          <div class="kid-progress-track"><div class="kid-progress-fill" style="width:{month_goal_pct}%;"></div></div>
          <div class="kid-progress-number">{month_pts} / {month_goal} 分</div>
        </div>
        <div class="kid-progress-line">
          <div class="kid-progress-name">本學期</div>
          <div class="kid-progress-track"><div class="kid-progress-fill" style="width:{term_goal_pct}%;"></div></div>
          <div class="kid-progress-number">{total_pts} / {term_goal} 分</div>
        </div>
        <div class="kid-insight-grid">
          <div class="kid-insight-card">
            <div class="kid-insight-label">徽章進度</div>
            <div class="kid-insight-value">{week_pts}/{nxt}</div>
            <div class="kid-insight-note">{badge_tip}</div>
          </div>
          <div class="kid-insight-card">
            <div class="kid-insight-label">親子共讀</div>
            <div class="kid-insight-value">{len(done_items)} / {coread_total}</div>
            <div class="kid-insight-note">完成率 {coread_done_pct}% · 待補寫 {len(todo_items)} 筆</div>
          </div>
          <div class="kid-insight-card">
            <div class="kid-insight-label">閱讀主題</div>
            <div class="kid-insight-value">{top_sdg_text}</div>
            <div class="kid-insight-note">{focus_text}</div>
          </div>
        </div>
      </div>
    </section>

    <div class="kid-reading-grid">
      <section class="kid-panel kid-chart-section">
        <div class="kid-panel-head">
          <div>
            <div class="kid-reading-kicker">SDG</div>
            <h4>本月 SDG 圖表</h4>
          </div>
        </div>
        <div class="kid-panel-body">
          {sdg_chart_html}
          <div class="kid-week-sdg">
            <div class="kid-mini-title">本週涵蓋主題</div>
            <div class="d-flex flex-wrap">{sdg_badges}</div>
          </div>
        </div>
      </section>

      <section class="kid-panel kid-recent-section">
        <div class="kid-panel-head">
          <div>
            <div class="kid-reading-kicker">Latest</div>
            <h4>最近閱讀紀錄</h4>
          </div>
          <span class="kid-pill">最新 5 筆</span>
        </div>
        <div class="kid-panel-body">
          <div class="kid-history-list">{recent_cards_html}</div>
        </div>
      </section>
    </div>
    """

    coread_tab_html = f"""
    <div class="kid-coread-page">
      <div class="kid-coread-summary">
        <div class="kid-coread-metric">
          <div class="kid-coread-metric-value">{len(todo_items)}</div>
          <div class="kid-coread-metric-label">待補寫</div>
        </div>
        <div class="kid-coread-metric">
          <div class="kid-coread-metric-value">{len(done_items)}</div>
          <div class="kid-coread-metric-label">已完成</div>
        </div>
        <div class="kid-coread-metric">
          <div class="kid-coread-metric-value">{coread_total}</div>
          <div class="kid-coread-metric-label">老師指定</div>
        </div>
        <div class="kid-coread-metric">
          <div class="kid-coread-metric-value">{blocked_coread}</div>
          <div class="kid-coread-metric-label">需孩子修正</div>
        </div>
      </div>
      <section class="kid-panel kid-coread-section">
        <div class="kid-panel-head">
          <div>
            <div class="kid-reading-kicker">Todo</div>
            <h4>待補寫：親子共讀</h4>
            <div class="kid-muted mt-1">完成家長心得後，老師就能看到可審核的完整資料。</div>
          </div>
          <a class="btn kid-soft-btn" href="{url_for('parent')}">返回家長首頁</a>
        </div>
        <div class="kid-panel-body">
        {todo_html}
        </div>
      </section>
      <section class="kid-panel kid-coread-section">
        <div class="kid-panel-head">
          <div>
            <div class="kid-reading-kicker">Done</div>
            <h4>已完成的親子共讀</h4>
          </div>
        </div>
        <div class="kid-panel-body">
          {done_html}
        </div>
      </section>
    </div>
    """

    history_tab_html = f"""
    <section class="kid-panel">
      <div class="kid-panel-head">
        <div>
          <div class="kid-reading-kicker">History</div>
          <h4>閱讀認證歷程</h4>
        </div>
        <span class="kid-pill">最近 40 筆</span>
      </div>
      <div class="kid-panel-body">
        <div class="kid-muted mb-3">點開學生心得可以查看孩子填寫的內容與照片。</div>
        <div class="table-responsive kid-history-table-wrap">
          <table class="table align-middle kid-history-table">
            <thead>
              <tr>
                <th style="width:160px;">時間</th>
                <th>內容</th>
                <th style="width:160px;">親子共讀</th>
                <th style="width:160px;" class="text-end">操作</th>
              </tr>
            </thead>
            <tbody>
              {history_rows}
            </tbody>
          </table>
        </div>
      </div>
    </section>
    """

    dashboard_style = """
    <style>
    .kid-reading-page{display:flex;flex-direction:column;gap:1rem;}
    .kid-reading-top{display:flex;justify-content:space-between;align-items:flex-end;gap:1rem;flex-wrap:wrap;margin-bottom:.8rem;}
    .kid-reading-title h4{margin:0;color:#162318;font-weight:900;letter-spacing:-.02em;}
    .kid-muted{color:#647067;font-size:.94rem;}
    .kid-reading-kicker{font-size:.78rem;font-weight:900;letter-spacing:.1em;text-transform:uppercase;color:#4f8f61;margin-bottom:.25rem;}
    .kid-reading-hero{position:relative;overflow:hidden;border-radius:30px;padding:1.35rem;background:linear-gradient(135deg,#eefcf2 0%,#fff7dd 58%,#eef7ff 100%);border:1px solid rgba(36,92,59,.1);box-shadow:0 24px 60px rgba(31,79,50,.12);}
    .kid-reading-hero:before{content:"";position:absolute;right:-5rem;top:-5rem;width:18rem;height:18rem;border-radius:50%;background:rgba(89,167,113,.16);}
    .kid-reading-hero-inner{position:relative;display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;flex-wrap:wrap;}
    .kid-reading-hero h3,.kid-panel h4{margin:0;color:#162318;font-weight:900;letter-spacing:-.02em;}
    .kid-reading-hero-note{border-radius:18px;background:rgba(255,255,255,.82);border:1px solid rgba(35,92,59,.1);padding:.75rem .9rem;min-width:12rem;text-align:right;}
    .kid-reading-hero-note span{display:block;color:#718078;font-size:.82rem;font-weight:850;}
    .kid-reading-hero-note strong{display:block;color:#183d28;font-size:.98rem;margin-top:.1rem;}
    .kid-metric-grid{position:relative;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem;margin-top:1rem;}
    .kid-metric{background:rgba(255,255,255,.86);border:1px solid rgba(35,92,59,.1);border-radius:20px;padding:.95rem 1rem;box-shadow:0 14px 32px rgba(24,76,46,.08);}
    .kid-metric-value{font-size:1.55rem;line-height:1;font-weight:900;color:#183d28;margin-bottom:.35rem;}
    .kid-metric-label{font-size:.88rem;color:#2f4738;font-weight:850;}
    .kid-metric-sub{font-size:.78rem;color:#7b887f;margin-top:.12rem;}
    .kid-tabs{border-radius:999px;background:#f5faf6;border:1px solid rgba(19,56,35,.08);padding:.35rem;gap:.35rem;display:inline-flex;flex-wrap:wrap;}
    .kid-tabs .nav-link{border-radius:999px!important;color:#435244!important;font-weight:900!important;padding:.55rem 1rem!important;}
    .kid-tabs .nav-link.active{background:#34784a!important;color:#fff!important;box-shadow:0 10px 24px rgba(52,120,74,.22);}
    .kid-soft-btn{border-radius:999px!important;border:1px solid rgba(35,92,59,.18)!important;background:#fff!important;color:#244c32!important;font-weight:850!important;padding:.5rem .95rem!important;box-shadow:0 10px 24px rgba(24,76,46,.08);}
    .kid-soft-btn--green{background:#34784a!important;color:#fff!important;border-color:#34784a!important;}
    .kid-soft-btn--green:hover{background:#28613b!important;color:#fff!important;}
    .kid-panel{background:rgba(255,255,255,.97);border:1px solid rgba(19,56,35,.08);border-radius:28px;box-shadow:0 22px 54px rgba(24,76,46,.1);overflow:hidden;}
    .kid-panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;padding:1.1rem 1.2rem;border-bottom:1px solid rgba(19,56,35,.08);background:linear-gradient(180deg,#ffffff 0%,#fbfdf8 100%);}
    .kid-panel-body{padding:1.1rem 1.2rem;}
    .kid-pill{display:inline-flex;align-items:center;border-radius:999px;background:#f3faf5;border:1px solid rgba(35,92,59,.1);color:#45624c;font-size:.84rem;font-weight:900;padding:.38rem .7rem;}
    .kid-progress-line{display:grid;grid-template-columns:7rem minmax(0,1fr) 8.5rem;gap:.75rem;align-items:center;margin:.75rem 0;}
    .kid-progress-name{font-weight:900;color:#244c32;}
    .kid-progress-track{height:.72rem;border-radius:999px;background:#e8f2ea;overflow:hidden;}
    .kid-progress-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,#34784a,#8dcf72);}
    .kid-progress-number{text-align:right;font-weight:850;color:#647067;font-size:.88rem;}
    .kid-insight-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.8rem;margin-top:1rem;}
    .kid-insight-card{border-radius:20px;background:linear-gradient(180deg,#ffffff 0%,#f6fbf7 100%);border:1px solid rgba(35,92,59,.08);padding:1rem;}
    .kid-insight-label{font-size:.82rem;font-weight:900;color:#4f8f61;letter-spacing:.05em;text-transform:uppercase;}
    .kid-insight-value{font-size:1.22rem;font-weight:900;color:#183d28;margin:.25rem 0;line-height:1.25;}
    .kid-insight-note{font-size:.86rem;color:#647067;line-height:1.5;}
    .kid-reading-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1rem;}
    .kid-chart-section,.kid-recent-section{grid-column:1/-1;}
    .kid-sdg-list{display:flex;flex-direction:column;gap:.7rem;}
    .kid-sdg-row{display:grid;grid-template-columns:9.6rem minmax(0,1fr) 4.2rem;gap:.75rem;align-items:center;}
    .kid-sdg-name{font-weight:850;color:#2d4736;font-size:.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .kid-sdg-track{height:.72rem;border-radius:999px;background:#e8f2ea;overflow:hidden;}
    .kid-sdg-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,#34784a,#8dcf72);}
    .kid-sdg-score{font-size:.86rem;font-weight:850;color:#647067;text-align:right;}
    .kid-week-sdg{border-top:1px solid rgba(19,56,35,.08);margin-top:1rem;padding-top:1rem;}
    .kid-mini-title{font-weight:900;color:#183d28;margin-bottom:.55rem;}
    .kid-history-list{display:flex;flex-direction:column;gap:.65rem;}
    .kid-history-card{display:flex;justify-content:space-between;align-items:center;gap:.9rem;border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:.85rem .95rem;}
    .kid-history-main{min-width:0;}
    .kid-history-title{font-weight:900;color:#17231b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .kid-history-meta{font-size:.86rem;color:#647067;margin-top:.15rem;}
    .kid-history-side{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:.25rem;flex-shrink:0;}
    .kid-history-score{font-size:.86rem;color:#647067;font-weight:850;}
    .kid-history-tag{display:inline-flex;margin-left:.3rem;border-radius:999px;font-size:.75rem;font-weight:850;padding:.12rem .45rem;}
    .kid-history-tag--ok{background:#e5f4ea;color:#2f7447;}
    .kid-history-tag--todo{background:#fff3cd;color:#8a5b12;}
    .kid-empty{border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;color:#718078;font-size:.94rem;}
    .kid-coread-page{display:flex;flex-direction:column;gap:1rem;}
    .kid-coread-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem;}
    .kid-coread-metric{border-radius:20px;background:linear-gradient(180deg,#fff 0%,#f8fbf6 100%);border:1px solid rgba(35,92,59,.1);box-shadow:0 14px 30px rgba(24,76,46,.08);padding:1rem;}
    .kid-coread-metric-value{font-size:1.65rem;font-weight:900;color:#183d28;line-height:1;}
    .kid-coread-metric-label{font-size:.86rem;font-weight:850;color:#647067;margin-top:.35rem;}
    .kid-coread-section{border-radius:24px!important;overflow:hidden;}
    .kid-coread-card{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;border-radius:18px;background:#fff;border:1px solid rgba(19,56,35,.08);padding:1rem;margin-top:.7rem;}
    .kid-coread-card--todo{background:linear-gradient(135deg,#fff8e7 0%,#f5fff7 100%);border-color:rgba(209,142,36,.22);}
    .kid-coread-title{font-weight:900;color:#17231b;font-size:1.02rem;}
    .kid-coread-muted{color:#647067;font-size:.9rem;margin-top:.15rem;}
    .kid-coread-action{display:flex;flex-direction:column;align-items:flex-end;gap:.25rem;}
    .kid-coread-empty{border-radius:18px;background:#f8fbf6;border:1px solid rgba(19,56,35,.08);padding:1rem;color:#718078;font-size:.94rem;margin-top:.7rem;}
    .kid-history-table-wrap{border-radius:20px;border:1px solid rgba(19,56,35,.08);overflow:hidden;}
    .kid-history-table{margin-bottom:0;}
    .kid-history-table thead th{background:#f4faf5;color:#244c32;font-size:.86rem;font-weight:900;border-bottom:1px solid rgba(19,56,35,.08);}
    .kid-history-table td{vertical-align:middle;}
    @media (max-width:1199.98px){.kid-metric-grid,.kid-coread-summary{grid-template-columns:repeat(2,minmax(0,1fr));}.kid-reading-grid{grid-template-columns:1fr;}}
    @media (max-width:767.98px){.kid-reading-hero,.kid-panel{border-radius:22px;}.kid-metric-grid,.kid-insight-grid,.kid-coread-summary{grid-template-columns:1fr;}.kid-progress-line,.kid-sdg-row{grid-template-columns:1fr;gap:.35rem;}.kid-progress-number,.kid-sdg-score{text-align:left;}.kid-reading-hero-note{text-align:left;width:100%;}.kid-history-card{align-items:flex-start;flex-direction:column;}.kid-history-side{text-align:left;align-items:flex-start;}.kid-coread-action{align-items:flex-start;}}
    </style>
    """

    content = f"""
    {dashboard_style}
    <div class="kid-reading-page">
      <div class="kid-reading-top">
        <div class="kid-reading-title">
          <div class="kid-reading-kicker">Parent View</div>
          <h4 class="mb-1">孩子閱讀儀表板</h4>
          <div class="small text-muted">家長：{parent_name}</div>
        </div>
        <a class="btn kid-soft-btn" href="{url_for('parent')}">返回家長首頁</a>
      </div>

      {overview_html}
      {coread_tab_html}
      {history_tab_html}
    </div>
    """

    return page("孩子閱讀儀表板", content)


@app.route("/edit_task/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    t = Task.query.get_or_404(task_id)
    try:
        allowed = can_manage_task(current_user, t)
    except NameError:
        
        allowed = (
            (current_user.role == "admin") or
            (current_user.role == "leader" and t.unit == current_user.unit) or
            (t.created_by == current_user.username) or
            (current_user.role == "teacher" and t.is_school_wide == 0 and
             t.unit == current_user.unit and t.grade == current_user.grade and t.class_no == current_user.class_no)
        )
    if not allowed:
        return toast_redirect(role_endpoint(current_user.role), "無權限。", "warning")

    g = request.form.get
    if request.method == "POST":
        
        t.title = g("title") or t.title
        t.description = g("description") or ""

        
        t.task_type = "homework"
        t.category = g("category") or (t.category or "其他")
        t.mission_category = None
        t.is_view_only = 1

        
        if hasattr(t, "points"):
            t.points = 0

        
        t.end_date = parse_date(g("end_date"))
        img_file = request.files.get("image")
        saved_img = save_image(img_file) if (img_file and getattr(img_file, "filename", "").strip()) else None
        _set_task_image_if_possible(t, saved_img)

        db.session.commit()
        return toast_redirect(role_endpoint(current_user.role), "已更新。", "success")

    
    hw_opts = "".join([
        f"<option value='{c}' {'selected' if ((t.category or '其他') == c) else ''}>{c}</option>"
        for c in CATEGORY_OPTIONS
    ])

    end_val = t.end_date.isoformat() if getattr(t, "end_date", None) else ""
    start_val = t.start_date.isoformat() if getattr(t, "start_date", None) else ""
    scope = f"{scope_txt(t)} · {t.unit or ''}"
    current_img = img_html(task_img_name(t), maxw=260)

    form = (
        "<form method='post' enctype='multipart/form-data' style='max-width:680px;'>"
          f"<div class='mb-3'><label class='form-label'>標題</label>"
          f"<input name='title' class='form-control' value='{t.title}' required></div>"

          "<div class='row g-3'>"
            f"<div class='col-md-6'>"
            f"<label class='form-label'>科目分類</label>"
            f"<select name='category' class='form-select'>{hw_opts}</select>"
            "</div>"

            f"<div class='col-md-3'><label class='form-label'>截止日</label>"
            f"<input type='date' name='end_date' class='form-control' value='{end_val}'></div>"

            f"<div class='col-md-3'><label class='form-label'>起始日</label>"
            f"<input class='form-control' value='{start_val}' disabled></div>"
          "</div>"

          f"<div class='mb-3 mt-3'><label class='form-label'>描述</label>"
          f"<textarea name='description' class='form-control' rows='3' required>{t.description or ''}</textarea></div>"
          f"{('<div class=\"mb-3\"><label class=\"form-label\">目前附圖</label>' + current_img + '</div>') if current_img else ''}"
          "<div class='mb-3'><label class='form-label'>更換附圖（可留空）</label>"
          "<input type='file' name='image' accept='.png,.jpg,.jpeg,.gif' class='form-control'></div>"

          f"<div class='form-text'>範圍：{scope} · 起始日（發布日）：{start_val or '—'}</div>"

          "<button class='btn btn-success'>儲存修改</button> "
          f"<a href='/{role_endpoint(current_user.role)}' class='btn btn-outline-secondary'>返回</a>"
        "</form>"
    )
    return page("修改作業", form)


@app.route("/delete_task/<int:task_id>")
@login_required
def delete_task(task_id):
    t = Task.query.get_or_404(task_id)
    try:
        allowed = can_manage_task(current_user, t)
    except NameError:
        allowed = (
            (current_user.role == "admin") or
            (current_user.role == "leader" and t.unit == current_user.unit) or
            (t.created_by == current_user.username) or
            (current_user.role == "teacher" and t.is_school_wide == 0 and
             t.unit == current_user.unit and t.grade == current_user.grade and t.class_no == current_user.class_no)
        )
    if not allowed:
        return toast_redirect(role_endpoint(current_user.role), "無權限。", "warning")

    try:
        img_name = _task_img_name(t)
        if img_name:
            try:
                os.remove(os.path.join(app.config.get("UPLOAD_FOLDER") or UPLOAD_DIR, _safe_upload_name(img_name)))
            except Exception:
                pass
        CompletedTask.query.filter_by(task_id=task_id).delete()
        db.session.delete(t)
        db.session.commit()
        return toast_redirect(role_endpoint(current_user.role), "作業已刪除。", "success")
    except Exception:
        db.session.rollback()
        return toast_redirect(role_endpoint(current_user.role), "刪除失敗。", "warning")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_columns()
        removed = 0
        removed += User.query.filter_by(username="admin").delete(synchronize_session=False)
        removed += User.query.filter_by(role="admin").delete(synchronize_session=False)
        if removed:
            db.session.commit()
    app.run(host="127.0.0.1", port=5001, debug=False)
