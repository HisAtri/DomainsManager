from . import sqlite
from modules.domain import Domain

db = sqlite

def domain_values(domain: Domain) -> dict:
    """
    提取 Domain 中的数据
    :param domain:
    :return:
    """
    data: dict = dict()
    data['domain'] = domain.domain
    data['reg_time'] = domain.reg_time
    data['exp_time'] = domain.exp_time
    data['upd_time'] = domain.upd_time
    data['active'] = domain.active
    data.update(domain.datas)
    return data
