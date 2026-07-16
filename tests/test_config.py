def test_secret_is_not_the_old_hardcoded_value(app):
    assert app.config["JWT_SECRET_KEY"] != "a135b8778fe5dc203c82a9fcb0bcce63a7bd62f4e72cdaf5649569168bb32b04"
