def test_drone_update_location(sample_drone):
    initial_lat = sample_drone.lat
    initial_lng = sample_drone.lng
    sample_drone.speed = 10.0
    sample_drone.direction = 0.0  # 正北
    sample_drone.update_location(1.0)
    # 向北移动约 10 米
    delta_lat = sample_drone.lat - initial_lat
    # 1度纬度 ≈ 111320 米，10米 ≈ 0.0000898度 ≈ 898 个单位 (1e7)
    assert abs(delta_lat - 898) < 100  # 允许误差

def test_drift_kinematics(sample_drone):
    original_speed = sample_drone.speed
    sample_drone.drift_kinematics()
    # 速度应在合理范围内变化（±1.5）
    assert abs(sample_drone.speed - original_speed) <= 1.5
    assert 0 <= sample_drone.geodetic_altitude <= 120