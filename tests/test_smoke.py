from sarcade.main import application_info


def test_application_info():
    info = application_info()
    assert info["name"] == "SARCADE Server"
    assert info["status"] == "initializing"
