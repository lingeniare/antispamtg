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


def test_new_defaults():
    s = Settings()
    assert s.mute_policy == "permanent"
    assert s.vision_mode == "suspect"
    assert s.probation_hours == 24
    assert s.probation_msgs == 5
    assert s.bio_scan is True
    assert s.vega_vision_model == ""
    assert s.rate_limit_count == 6
    assert s.rate_window_sec == 10
