from drone_rid_spoofer.config_validator import validate_config

def test_valid_config():
    config = {
        "global": {"interface": "wlan1", "interval": 1.0, "location": [23.14, 113.27]},
        "drones": [{"mode": "random", "serial": "TEST"}]
    }
    errors = validate_config(config)
    assert errors == []

def test_invalid_missing_global():
    config = {"drones": []}
    errors = validate_config(config)
    assert "Missing 'global' section" in errors[0]