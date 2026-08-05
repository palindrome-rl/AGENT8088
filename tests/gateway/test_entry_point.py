def test_gateway_main_exists():
    from agent8088.gateway import main
    assert callable(main)


def test_gateway_main_imports_without_error():
    import agent8088.gateway
    assert hasattr(agent8088.gateway, "main")