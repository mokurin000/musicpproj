from flask import Flask, request, jsonify, send_from_directory, session, Response
from flask_cors import CORS
import os
import subprocess
import uuid
import logging
import convert
import pymongo
from pymongo import MongoClient, errors
from bson.objectid import ObjectId
import gridfs
from datetime import datetime
import bcrypt
from dotenv import load_dotenv
from functools import wraps

# 加载环境变量
load_dotenv()

# 初始化应用
app = Flask(__name__)
CORS(app, origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000"))
app.secret_key = os.getenv("SECRET_KEY", "fallback-secret-key")
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# 配置参数
UPLOAD_FOLDER = os.path.abspath("uploads")
ALLOWED_EXTENSIONS = {"pdf"}
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15MB
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MongoDB配置
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
db = client[os.getenv("DB_NAME", "midi_files_db")]
midi_collection = db["midi_files"]
users_collection = db["users"]
social_collection = db["social_files"]  # 新增社交集合
fs = gridfs.GridFS(db)

# 创建索引
try:
    users_collection.create_index([("username", pymongo.ASCENDING)], unique=True)
    midi_collection.create_index([("task_id", pymongo.ASCENDING)])
    social_collection.create_index([("original_file_id", pymongo.ASCENDING)])
except errors.PyMongoError as e:
    logger.error(f"索引创建失败: {str(e)}")

# 任务状态存储（建议替换为Redis）
task_statuses = {}


# ------------------------- 装饰器 -------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "需要登录"}), 401
        return f(*args, **kwargs)

    return decorated


# ------------------------- 工具函数 -------------------------
def get_task_status(task_id):
    return task_statuses.get(task_id, "not_found")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_username(user_id):
    user = users_collection.find_one({"_id": user_id}, {"username": 1})
    return user["username"] if user else "未知用户"


# ------------------------- 用户认证路由 -------------------------
@app.route("/api/register", methods=["POST"])
def api_register():
    try:
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        birthday = request.form.get("birthday", "").strip()

        if not all([username, password, birthday]):
            return jsonify({"success": False, "message": "所有字段均为必填"}), 400

        if len(password) < 6:
            return jsonify({"success": False, "message": "密码至少需要6位"}), 400

        if users_collection.find_one({"username": username}):
            return jsonify({"success": False, "message": "用户名已存在"}), 400

        hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

        users_collection.insert_one(
            {
                "username": username,
                "password": hashed_pw,
                "birthday": birthday,
                "created_at": datetime.now(),
                "favorites": [],  # 新增收藏字段
            }
        )

        return jsonify(
            {"success": True, "message": "注册成功", "redirect": "/login"}
        ), 201

    except errors.DuplicateKeyError:
        return jsonify({"success": False, "message": "用户名已存在"}), 400
    except errors.PyMongoError as e:
        logger.error(f"数据库错误: {str(e)}")
        return jsonify({"success": False, "message": "数据库错误"}), 500
    except Exception as e:
        logger.error(f"服务器错误: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": "服务器内部错误"}), 500


@app.route("/api/login", methods=["POST"])
def api_login():
    try:
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = users_collection.find_one({"username": username})

        if user and bcrypt.checkpw(
            password.encode("utf-8"), user["password"].encode("utf-8")
        ):
            session["user_id"] = str(user["_id"])
            return jsonify({"success": True, "redirect": "/"}), 200
        return jsonify({"success": False, "message": "用户名或密码错误"}), 401

    except Exception as e:
        logger.error(f"登录失败: {str(e)}")
        return jsonify({"success": False, "message": "登录失败"}), 500


@app.route("/api/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    return jsonify({"success": True, "message": "已登出"}), 200


# ------------------------- 文件处理路由 -------------------------
@app.route("/api/upload", methods=["POST"])
@login_required
def convert_pdf_to_midi():
    if "pdf" not in request.files:
        return jsonify({"success": False, "message": "未找到文件部分"}), 400

    file = request.files["pdf"]
    if not (file and allowed_file(file.filename)):
        return jsonify({"success": False, "message": "无效文件类型"}), 400

    file.seek(0, os.SEEK_END)
    if file.tell() > MAX_FILE_SIZE:
        return jsonify({"success": False, "message": "文件超过15MB限制"}), 413
    file.seek(0)

    file_id = str(uuid.uuid4())
    pdf_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.pdf")

    try:
        file.save(pdf_path)
        with open(pdf_path, "rb") as f:
            pdf_id = fs.put(f, filename=file.filename, task_id=file_id)

        midi_collection.insert_one(
            {
                "task_id": file_id,
                "user_id": session["user_id"],
                "filename": file.filename,
                "file_id": str(pdf_id),
                "file_type": "pdf",
                "created_at": datetime.now(),
                "is_shared": False,  # 新增分享状态字段
                "share_count": 0,  # 新增分享次数字段
            }
        )

        task_statuses[file_id] = "uploaded"
        return jsonify(
            {
                "success": True,
                "task_id": file_id,
                "next_step_url": f"/api/convert/{file_id}",
            }
        )

    except Exception as e:
        logger.error(f"上传失败: {str(e)}")
        return jsonify({"success": False, "message": "服务器错误"}), 500


# ------------------------- 社交功能路由 -------------------------
@app.route("/api/user/files", methods=["GET"])
@login_required
def get_user_files():
    try:
        files = list(
            midi_collection.find(
                {"user_id": session["user_id"]},
                {
                    "_id": 0,
                    "task_id": 1,
                    "filename": 1,
                    "created_at": 1,
                    "is_shared": 1,
                },
            )
        )
        return jsonify(
            [{**f, "created_at": f["created_at"].isoformat()} for f in files]
        ), 200
    except Exception as e:
        logger.error(f"获取用户文件失败: {str(e)}")
        return jsonify({"success": False, "message": "获取文件失败"}), 500


@app.route("/api/share/<file_id>", methods=["POST"])
@login_required
def share_file(file_id):
    try:
        # 验证文件所有权
        file = midi_collection.find_one(
            {"task_id": file_id, "user_id": session["user_id"]}
        )

        if not file:
            return jsonify({"success": False, "message": "文件不存在"}), 404

        # 创建社交记录
        social_collection.insert_one(
            {
                "original_file_id": file["_id"],
                "share_user_id": ObjectId(session["user_id"]),
                "likes": [],
                "favorites": [],
                "comments": [],
                "created_at": datetime.now(),
            }
        )

        # 更新原文件状态
        midi_collection.update_one(
            {"_id": file["_id"]},
            {"$set": {"is_shared": True}, "$inc": {"share_count": 1}},
        )

        return jsonify({"success": True, "message": "分享成功"}), 200

    except Exception as e:
        logger.error(f"分享失败: {str(e)}")
        return jsonify({"success": False, "message": "分享失败"}), 500


# 修改获取动态的接口
@app.route("/api/feed", methods=["GET"])
@login_required
def get_social_feed():
    try:
        current_user_id = ObjectId(session["user_id"])

        pipeline = [
            # 第一阶段：联查原始文件信息
            {
                "$lookup": {
                    "from": "midi_files",
                    "localField": "original_file_id",
                    "foreignField": "_id",
                    "as": "file_info",
                }
            },
            {"$unwind": "$file_info"},
            # 第二阶段：联查分享用户信息
            {
                "$lookup": {
                    "from": "users",
                    "localField": "share_user_id",
                    "foreignField": "_id",
                    "as": "user_info",
                }
            },
            {"$unwind": "$user_info"},
            # 第三阶段：展开评论并关联用户信息
            {
                "$unwind": {
                    "path": "$comments",
                    "preserveNullAndEmptyArrays": True,  # 保留没有评论的文档
                }
            },
            {
                "$lookup": {
                    "from": "users",
                    "localField": "comments.user_id",
                    "foreignField": "_id",
                    "as": "comment_user",
                }
            },
            {"$unwind": {"path": "$comment_user", "preserveNullAndEmptyArrays": True}},
            # 第四阶段：重组评论结构
            {
                "$group": {
                    "_id": "$_id",
                    "original_file_id": {"$first": "$original_file_id"},
                    "share_user_id": {"$first": "$share_user_id"},
                    "file_info": {"$first": "$file_info"},
                    "user_info": {"$first": "$user_info"},
                    "likes": {"$first": "$likes"},
                    "created_at": {"$first": "$created_at"},
                    "comments": {
                        "$push": {
                            "_id": "$comments._id",
                            "content": "$comments.content",
                            "created_at": "$comments.created_at",
                            "user_id": "$comment_user._id",
                            "username": "$comment_user.username",
                        }
                    },
                }
            },
            # 第五阶段：添加计算字段
            {
                "$addFields": {
                    "is_liked": {"$in": [current_user_id, "$likes"]},
                    "likes_count": {"$size": "$likes"},
                    "comments_count": {"$size": "$comments"},
                    "can_manage": {"$eq": ["$share_user_id", current_user_id]},
                }
            },
            # 第六阶段：项目阶段整理输出格式
            {
                "$project": {
                    "_id": 0,
                    "social_id": {"$toString": "$_id"},
                    "file_id": {"$toString": "$file_info._id"},
                    "title": "$file_info.filename",
                    "author": "$user_info.username",
                    "author_id": {"$toString": "$user_info._id"},
                    "created_at": 1,
                    "likes_count": 1,
                    "comments_count": 1,
                    "is_liked": 1,
                    "can_manage": 1,
                    "comments": {
                        "$map": {
                            "input": "$comments",
                            "as": "comment",
                            "in": {
                                "comment_id": {"$toString": "$$comment._id"},
                                "content": "$$comment.content",
                                "created_at": "$$comment.created_at",
                                "user_id": {"$toString": "$$comment.user_id"},
                                "username": "$$comment.username",
                                "can_delete": {
                                    "$eq": ["$$comment.user_id", current_user_id]
                                },
                            },
                        }
                    },
                }
            },
            # 第七阶段：排序（按时间倒序）
            {"$sort": {"created_at": -1}},
            # 第八阶段：分页控制
            {"$skip": request.args.get("page", 0, type=int) * 10},
            {"$limit": 10},
        ]

        feeds = list(social_collection.aggregate(pipeline))

        for feed in feeds:
            task = midi_collection.find_one({"_id": ObjectId(feed["file_id"])})
            if task is not None:
                feed["task_id"] = task["task_id"]

        return jsonify(
            [
                {
                    **f,
                    "created_at": f["created_at"].isoformat(),
                    "current_user_id": str(current_user_id),  # 返回当前用户ID供前端比对
                }
                for f in feeds
            ]
        ), 200

    except Exception as e:
        logger.error(f"获取动态失败: {str(e)}")
        return jsonify({"success": False, "message": "获取动态失败"}), 500


# ------------------------- 点赞路由 -------------------------
@app.route("/api/like/<social_id>", methods=["POST"])
@login_required
def toggle_like(social_id):
    try:
        user_id = ObjectId(session["user_id"])
        action = request.json.get("action", "like")

        # 验证社交文档存在性
        social_doc = social_collection.find_one({"_id": ObjectId(social_id)})
        if not social_doc:
            return jsonify({"success": False, "message": "动态不存在"}), 404

        # 构建更新操作
        update_operation = {
            "like": {"$addToSet": {"likes": user_id}},
            "unlike": {"$pull": {"likes": user_id}},
        }.get(action)

        if not update_operation:
            return jsonify({"success": False, "message": "无效操作类型"}), 400

        # 执行更新
        result = social_collection.update_one(
            {"_id": ObjectId(social_id)}, update_operation
        )

        if result.modified_count == 0:
            return jsonify({"success": False, "message": "操作失败"}), 400

        # 获取更新后的点赞数
        updated_doc = social_collection.find_one({"_id": ObjectId(social_id)})
        return jsonify({"success": True, "likes_count": len(updated_doc["likes"])}), 200

    except errors.PyMongoError as e:
        logger.error(f"数据库操作失败: {str(e)}")
        return jsonify({"success": False, "message": "数据库错误"}), 500
    except Exception as e:
        logger.error(f"服务器错误: {str(e)}")
        return jsonify({"success": False, "message": "服务器内部错误"}), 500


# ------------------------- 评论功能路由 -------------------------
@app.route("/api/comment/<social_id>", methods=["POST"])
@login_required
def add_comment(social_id):
    try:
        # 获取当前用户信息
        user_id = ObjectId(session["user_id"])
        content = request.json.get("content", "").strip()

        # 基础验证
        if not content:
            return jsonify({"success": False, "message": "评论内容不能为空"}), 400

        # 验证动态存在性
        if not social_collection.find_one({"_id": ObjectId(social_id)}):
            return jsonify({"success": False, "message": "动态不存在"}), 404

        # 构建评论对象
        new_comment = {
            "_id": ObjectId(),  # 新增唯一ID
            "user_id": user_id,
            "content": content,  # 添加你的内容过
            "created_at": datetime.now(),
        }

        # 更新数据库
        _ = social_collection.update_one(
            {"_id": ObjectId(social_id)}, {"$push": {"comments": new_comment}}
        )

        # 获取最新评论
        updated_doc = social_collection.find_one(
            {"_id": ObjectId(social_id)}, {"comments": {"$slice": -1}}
        )

        return jsonify(
            {
                "success": True,
                "new_comment": {
                    "content": content,
                    "author": get_username(user_id),
                    "time": updated_doc["comments"][-1]["created_at"].isoformat(),
                },
            }
        ), 200

    except errors.PyMongoError as e:
        logger.error(f"数据库错误: {str(e)}")
        return jsonify({"success": False, "message": "数据库错误"}), 500
    except Exception as e:
        logger.error(f"服务器错误: {str(e)}")
        return jsonify({"success": False, "message": "服务器内部错误"}), 500


# 修改现有的下载路由，增加预览功能
@app.route("/preview/<file_id>")
@login_required
def preview_file(file_id):
    """预览PDF文件"""
    try:
        # 验证文件ID格式
        if not ObjectId.is_valid(file_id):
            return jsonify({"error": "Invalid file ID"}), 400

        # 获取文件元数据
        file_entry = midi_collection.find_one({"file_id": file_id})
        if not file_entry:
            return jsonify({"error": "File not found"}), 404

        # 验证文件所有权
        if file_entry["user_id"] != session["user_id"]:
            return jsonify({"error": "Unauthorized access"}), 403

        # 获取文件数据
        file_data = fs.get(ObjectId(file_id)).read()

        # 设置预览头
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": f'inline; filename="{file_entry["filename"]}"',
        }

        return Response(file_data, headers=headers)
    except Exception as e:
        logger.error(f"Preview failed: {str(e)}")
        return jsonify({"success": False, "message": "文件预览失败"}), 500


@app.route("/api/get_pdf_id/<task_id>")
@login_required
def get_pdf_id(task_id):
    """获取PDF文件ID"""
    try:
        # 查找原始PDF记录
        pdf_entry = midi_collection.find_one(
            {"task_id": task_id, "file_type": "pdf", "user_id": session["user_id"]}
        )

        if not pdf_entry:
            return jsonify({"success": False, "message": "PDF not found"}), 404

        return jsonify({"success": True, "file_id": pdf_entry["file_id"]})
    except Exception as e:
        logger.error(f"Get PDF ID failed: {str(e)}")
        return jsonify({"success": False, "message": "获取文件失败"}), 500


@app.route("/api/convert/<task_id>", methods=["POST"])
def start_conversion(task_id):
    """启动文件转换流程"""
    logger.info(f"启动转换流程，任务 ID: {task_id}")
    try:
        status = get_task_status(task_id)

        if status != "uploaded":
            return jsonify({"success": False, "message": "任务状态无效"}), 400

        # 设置任务状态为 in_progress
        task_statuses[task_id] = "in_progress"
        # 获取原始PDF文件名
        pdf_entry = midi_collection.find_one({"task_id": task_id, "file_type": "pdf"})
        if not pdf_entry:
            raise ValueError("原始PDF记录不存在")
        original_filename = pdf_entry["filename"]
        midi_filename = f"{os.path.splitext(original_filename)[0]}.mid"

        pdf_path = os.path.join(UPLOAD_FOLDER, f"{task_id}.pdf")
        midi_path = os.path.join(UPLOAD_FOLDER, midi_filename)

        # Audiveris配置
        output_dir = UPLOAD_FOLDER

        # 使用绝对路径执行命令
        command = [
            "C:/Program Files/Audiveris/Audiveris.exe",
            "-batch",
            "-export",
            "-output",
            output_dir,
            "--",
            pdf_path,
        ]

        logger.info(f"执行命令: {' '.join(command)}")
        subprocess.run(
            command,
            check=True,
            shell=True,
            timeout=300,  # 设置5分钟超时
        )

        # 查找生成的MXL文件
        mxl_files = [f for f in os.listdir(output_dir) if f.endswith(".mxl")]
        if not mxl_files:
            raise FileNotFoundError(f"未找到MXL文件: {output_dir}")

        mxl_path = os.path.join(output_dir, mxl_files[0])
        logger.info(f"找到MXL文件: {mxl_path}")

        # 解压MXL文件
        # 修改为正确的脚本路径
        # unzip_script_path = r'/Unzip-MXL.ps1'
        unzip_script_path = os.path.join(os.getcwd(), "Unzip-MXL.ps1")
        if not os.path.exists(unzip_script_path):
            raise FileNotFoundError(f"未找到解压脚本: {unzip_script_path}")

        unzip_command = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            unzip_script_path,
            mxl_path,
            output_dir,
        ]

        subprocess.run(unzip_command, check=True, timeout=60)
        logger.info("成功解压MXL文件")

        # 查找XML文件
        xml_files = [f for f in os.listdir(output_dir) if f.endswith(".xml")]
        if not xml_files:
            raise FileNotFoundError("解压后未找到XML文件")

        musicxml_path = os.path.join(output_dir, xml_files[0])
        logger.info(f"找到MusicXML文件: {musicxml_path}")

        # 存储XML文件到MongoDB
        xml_data = open(musicxml_path, "rb").read()
        xml_id = fs.put(
            xml_data, filename=xml_files[0], task_id=task_id, file_type="xml"
        )
        midi_collection.insert_one(
            {
                "task_id": task_id,
                "filename": xml_files[0],
                "file_id": str(xml_id),
                "file_type": "xml",
                "created_at": datetime.now(),
            }
        )
        logger.info(f"成功存储XML文件到MongoDB，文件ID: {xml_id}")

        # 转换为MIDI
        convert.musicxml_to_midi(musicxml_path, midi_path)
        logger.info(f"生成MIDI文件: {midi_path}")

        # 存储MIDI文件到MongoDB
        midi_data = open(midi_path, "rb").read()
        midi_id = fs.put(
            midi_data, filename=midi_filename, task_id=task_id, file_type="mid"
        )
        midi_collection.insert_one(
            {
                "task_id": task_id,
                "filename": midi_filename,
                "file_id": str(midi_id),
                "file_type": "mid",
                "created_at": datetime.now(),
            }
        )
        logger.info(f"成功存储MIDI文件到MongoDB，文件ID: {midi_id}")

        # 设置任务状态为 completed
        task_statuses[task_id] = "completed"

        return jsonify(
            {
                "success": True,
                "message": "转换成功",
                "download_url": f"/download/{str(midi_id)}",  # 使用安全下载路径
                "midi_db_id": str(midi_id),
                "filename": midi_filename,  # 新增文件名字段
            }
        )

    except subprocess.CalledProcessError as e:
        logger.error(f"子进程错误: {str(e)}")
        task_statuses[task_id] = "failed"  # 设置任务状态为 failed
        return jsonify({"success": False, "message": "转换失败"}), 500

    except FileNotFoundError as e:
        logger.error(f"文件未找到: {str(e)}")
        task_statuses[task_id] = "failed"  # 设置任务状态为 failed
        return jsonify({"success": False, "message": str(e)}), 500

    except Exception as e:
        logger.error(f"未知错误: {str(e)}")
        task_statuses[task_id] = "failed"  # 设置任务状态为 failed
        return jsonify({"success": False, "message": "服务器错误"}), 500


@app.route("/api/status/<task_id>")
def get_conversion_status(task_id):
    """查询文件转换状态"""
    try:
        status = get_task_status(task_id)
        if status == "completed":
            midi_filename = f"{task_id}.mid"
            # 从MongoDB获取文件ID
            midi_db_entry = midi_collection.find_one(
                {"task_id": task_id, "file_type": "mid"}
            )
            if midi_db_entry:
                return jsonify(
                    {
                        "status": "completed",
                        "progress": 100,
                        "midi_file": midi_filename,
                        "midi_db_id": midi_db_entry["file_id"],
                    }
                )
            else:
                return jsonify(
                    {"status": "completed", "progress": 100, "midi_file": midi_filename}
                )
        elif status == "in_progress":
            return jsonify(
                {
                    "status": "in_progress",
                    "progress": 50,  # 示例进度
                }
            )
        elif status == "failed":
            return jsonify({"status": "failed", "message": "转换失败"}), 500
        elif status == "uploaded":
            return jsonify({"status": "uploaded", "message": "文件已上传，等待转换"})
        else:
            return jsonify({"status": "not_found", "message": "任务 ID 无效"}), 404
    except Exception as e:
        logger.error(f"查询状态失败: {str(e)}")
        return jsonify({"status": "failed", "message": "查询状态失败"}), 500


@app.route("/play_midi")
def play_midi_page():
    """播放MIDI文件的页面"""
    return send_from_directory("static", "play_midi.html")


@app.route("/")
def index_page():
    """播放MIDI文件的页面"""
    return send_from_directory("static", "index.html")


@app.route("/api/play/<midi_db_id>")
def play_midi_api(midi_db_id):
    """播放MIDI文件"""
    try:
        # 从MongoDB获取文件
        midi_db_entry = midi_collection.find_one({"file_id": midi_db_id})
        if not midi_db_entry:
            raise FileNotFoundError("文件未找到")

        file_id = ObjectId(midi_db_entry["file_id"])
        midi_data = fs.get(file_id).read()

        return Response(midi_data, mimetype="audio/midi")
    except Exception as e:
        logger.error(f"播放失败: {str(e)}")
        return jsonify({"success": False, "message": "播放失败"}), 404


@app.route("/api/get_midi_id/<task_id>")
@login_required
def get_midi_id(task_id):
    """获取MIDI文件ID"""
    try:
        # 查找MIDI文件记录
        midi_entry = midi_collection.find_one({"task_id": task_id, "file_type": "mid"})

        if not midi_entry:
            return jsonify({"success": False, "message": "MIDI文件未找到"}), 404

        return jsonify(
            {
                "success": True,
                "file_id": midi_entry["file_id"],
                "filename": midi_entry["filename"],
            }
        )
    except Exception as e:
        logger.error(f"获取MIDI ID失败: {str(e)}")
        return jsonify({"success": False, "message": "获取文件失败"}), 500


# 下载路由
@app.route("/download/<file_id>")
def download_midi(file_id):
    """通过文件ID下载MIDI文件"""
    try:
        # 验证文件ID格式
        if not ObjectId.is_valid(file_id):
            logger.error(f"无效的文件ID: {file_id}")
            return jsonify({"success": False, "message": "无效的文件ID"}), 400

        # 从MongoDB获取文件
        midi_db_entry = midi_collection.find_one({"file_id": file_id})
        if not midi_db_entry:
            logger.error(f"文件未找到: {file_id}")
            return jsonify({"success": False, "message": "文件未找到"}), 404

        # 获取实际文件名
        filename = midi_db_entry.get("filename", "download.mid")
        if not filename.endswith(".mid"):
            logger.error(f"无效的文件类型: {filename}")
            return jsonify({"success": False, "message": "无效的文件类型"}), 400

        # 获取文件数据
        try:
            file_data = fs.get(ObjectId(file_id)).read()
        except Exception as e:
            logger.error(f"文件数据获取失败: {str(e)}")
            return jsonify({"success": False, "message": "文件数据获取失败"}), 404

        return (
            file_data,
            200,
            {
                "Content-Type": "audio/midi",
                "Content-Disposition": f"attachment; filename={filename}",
            },
        )
    except Exception as e:
        logger.error(f"下载失败: {str(e)}")
        return jsonify({"success": False, "message": "文件下载失败"}), 500
    # 登录页面


@app.route("/login")
def login():
    return send_from_directory("static", "login.html")


# 检查登录状态
@app.route("/api/check_login")
def check_login():
    return jsonify({"logged_in": "user_id" in session})


# 注册页面
@app.route("/register")
def register():
    return send_from_directory("static", "register.html")


@app.route("/profile")
def profile():
    return send_from_directory("static", "profile.html")


@app.route("/social")
def social():
    return send_from_directory("static", "social.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=os.getenv("FLASK_DEBUG") == "True")
