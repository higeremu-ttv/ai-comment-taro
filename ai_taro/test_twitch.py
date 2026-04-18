import urllib.request, json
url = 'https://api.twitch.tv/helix/streams?user_login=higeremu'
req = urllib.request.Request(url)
req.add_header('Authorization', 'Bearer djd6fun3jigigxqtt1hzjjxoz4omt3')
req.add_header('Client-Id', 'ayqbqrjjmtfna0ljr0aon3rh1a9kt4')
try:
    with urllib.request.urlopen(req) as r:
        print(json.loads(r.read().decode()))
except Exception as e:
    print('ERROR:', e)