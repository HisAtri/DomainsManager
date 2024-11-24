from flask import request

from modules.server import *
from modules.domain import Domain
from db import db

@v1_bp.route("/domain/update/<path:domain>", methods=["POST"])
def update_domain(domain: str):
    """
    用户提交域名信息，数据库更新或创建
    :param domain:
    :return:
    """
    domain_data = request.json
    _domain: Domain = Domain(domain)
    _domain.update(domain_data)
    db.update_domain(_domain)
    return "OK"

@v1_bp.route("/domain/get/<path:key>", methods=["GET"])
def get_domain(key: str):
    """
    用户获取域名信息
    :param key: 域名UUID
    :return:
    """
    domain: Domain = db.get_domain(key)
    return domain.dump()

@v1_bp.route("/domain/get", methods=["GET"])
def get_domain_list():
    """
    获取活跃域名列表
    :return:
    """
    return db.get_active_domains()

@v1_bp.route("/domain/del/<path:key>", methods=["GET", "DELETE"])
def delete_domain(key: str):
    """
    删除域名（将其设为不活跃）
    :return:
    """
    domain: Domain = db.get_domain(uuid=key)
    if not isinstance(domain, Domain):
        return "Not Found", 404
    domain.active = False
    db.update_domain(domain)
    return "OK"
