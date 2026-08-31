import os
from src.config import Settings

def test_whitelist_parse():
    s = Settings(whitelist_users="123, -100456, abc, 789")
    assert 123 in s.whitelist_list
    assert -100456 in s.whitelist_list
    assert 789 in s.whitelist_list
    assert len(s.whitelist_list) == 3

def test_admin_parse():
    s = Settings(admin_user_ids="1,2, bad")
    assert s.admin_list == [1,2]
