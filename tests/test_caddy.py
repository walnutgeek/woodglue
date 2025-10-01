def test_caddy_config():
    from woodglue.utils.caddy import CaddyConfig

    expected = [
        "apps=AppsConfig(http=HTTPConfig(servers={'srv0': ServerConfig(id='site1.xyz', listen=[':443'], routes=[RouteConfig(group=None, match=[CaddyMatcherConfig(host=['site1.xyz'], path=None, method=None, protocol=None)], handle=[SubrouteHandler(handler='subroute', routes=[RouteConfig(group=None, match=None, handle=[VarsHandler(handler='vars', root='/var/www/site1'), FileServerHandler(handler='file_server', root=None)], terminal=None)])], terminal=True)])}))",
        "apps=AppsConfig(http=HTTPConfig(servers={'srv0': ServerConfig(id='v123.xyz', listen=[':443'], routes=[RouteConfig(group=None, match=[CaddyMatcherConfig(host=['v123.xyz'], path=None, method=None, protocol=None)], handle=[SubrouteHandler(handler='subroute', routes=[RouteConfig(group=None, match=None, handle=[EncodeHandler(handler='encode', encodings={'gzip': {}, 'zstd': {}}, prefer=['zstd', 'gzip']), ReverseProxyHandler(handler='reverse_proxy', upstreams=[Upstream(dial='localhost:5555', max_requests=None)])], terminal=None)])], terminal=True)]), 'srv1': ServerConfig(id=None, listen=[':80'], routes=[RouteConfig(group=None, match=None, handle=[VarsHandler(handler='vars', root='/usr/share/caddy'), FileServerHandler(handler='file_server', root=None)], terminal=None)])}))",
    ]
    for i in range(1, 3):
        config = CaddyConfig.model_validate_json(open(f"tests/caddy_stuff/config{i}.json").read())
        assert config is not None
        print(config)
        assert str(config) == expected[i - 1]
        json = config.model_dump_json(by_alias=True, exclude_none=True)
        config2 = CaddyConfig.model_validate_json(json)
        print(json)
        print(config2)
        assert str(config2) == expected[i - 1]

    # raise Exception("test passed")
