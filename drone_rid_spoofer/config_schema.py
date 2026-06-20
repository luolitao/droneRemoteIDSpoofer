# config_schema.py
CONFIG_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "global": {
            "type": "object",
            "properties": {
                "interface": {"type": "string"},
                "interval": {"type": "number", "minimum": 0.1},
                "location": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2
                },
                "transport": {"type": "string"},
                "protocol": {"type": "string", "enum": ["astm", "gb42590", "gb46750"]},
                "physical_transport": {"type": "string"},
                "wifi": {"type": "object"},
                "ble": {"type": "object"},
                "gb": {"type": "object"},
                "gb46750": {"type": "object"}
            },
            "additionalProperties": False
        },
        "drones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["random", "static", "waypoints"]},
                    "serial": {"type": "string", "maxLength": 20},
                    "protocol": {"type": "string", "enum": ["astm", "gb42590", "gb46750"]},
                    "physical_transport": {"type": "string"},
                    "transport": {"type": "string"},  # 兼容旧字段
                    "start_location": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                    "pilot_location": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                    "operator_id": {"type": "string"},
                    "operator_altitude": {"type": "number", "minimum": 0},
                    "speed": {"type": "number", "minimum": 0},
                    "vertical_speed": {"type": "number"},
                    "geodetic_altitude": {"type": "number", "minimum": 0, "maximum": 120},
                    "pressure_altitude": {"type": "number", "minimum": 0, "maximum": 120},
                    "height": {"type": "number", "minimum": 0, "maximum": 120},
                    "mac": {"type": "string", "pattern": "^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"},
                    "lifespan_seconds": {"type": "number", "minimum": 0},
                    "waypoints": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 3
                        }
                    },
                    "registration_mark": {"type": "string", "maxLength": 8},
                    "operation_category": {"type": "integer", "enum": [0, 1, 2, 3]},
                    "ua_classification": {"type": "integer", "enum": [0, 1, 2, 3, 4]},
                    "station_location_type": {"type": "integer", "enum": [0, 1]},
                    "horizontal_accuracy": {"type": "integer", "minimum": 0, "maximum": 12},
                    "vertical_accuracy": {"type": "integer", "minimum": 0, "maximum": 6},
                    "speed_accuracy": {"type": "integer", "minimum": 0, "maximum": 4},
                    "timestamp_accuracy": {"type": "integer", "minimum": 0, "maximum": 8},
                    # 其他可能字段...
                },
                "additionalProperties": False
            }
        }
    },
    "required": ["global"],
    "additionalProperties": False
}