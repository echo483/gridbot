from gridbot.ratelimit import RateLimiter


def test_allows_up_to_rpm_then_waits():
    t = [0.0]
    rl = RateLimiter(6, clock=lambda: t[0])
    for _ in range(6):
        assert rl._wait_needed() == 0.0
        rl._stamps.append(t[0])
        t[0] += 1.0
    assert rl._wait_needed() > 0.0


def test_window_rolls_off():
    t = [0.0]
    rl = RateLimiter(2, clock=lambda: t[0])
    rl._stamps = [0.0, 1.0]
    t[0] = 2.0
    assert rl._wait_needed() > 0.0
    t[0] = 61.5
    assert rl._wait_needed() == 0.0
