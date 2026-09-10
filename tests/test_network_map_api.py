import app as appmod


def test_network_node_is_tenant_owned_and_in_delete_order():
    assert appmod.NetworkNode in appmod.TENANT_OWNED_MODELS
    assert appmod.NetworkNode in appmod._TENANT_DELETE_ORDER


def test_node_kinds_are_exactly_the_three_documented_values():
    assert appmod.NODE_KINDS == ('root', 'junction', 'onu')


def test_to_dict_returns_coordinates_as_floats(app):
    from app import db
    with app.app_context():
        node = appmod.NetworkNode(
            tenant_id=1, olt_device_id=1, kind='root', label='Control Room',
            latitude=34.436700, longitude=35.849700)
        db.session.add(node)
        db.session.commit()
        # Round-trip through the database so Numeric columns become Decimal
        db.session.expire_all()
        node = db.session.get(appmod.NetworkNode, node.id)

        # The raw attribute after round-trip must be Decimal, proving the round-trip happened
        from decimal import Decimal
        assert isinstance(node.latitude, Decimal), \
            f"Expected Decimal after round-trip, got {type(node.latitude)}"
        assert isinstance(node.longitude, Decimal), \
            f"Expected Decimal after round-trip, got {type(node.longitude)}"

        # to_dict() must convert Decimal to float for JSON serialization
        data = node.to_dict()
        assert isinstance(data['latitude'], float)
        assert isinstance(data['longitude'], float)
        assert data['latitude'] == 34.4367
