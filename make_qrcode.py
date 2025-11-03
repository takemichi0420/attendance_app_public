import qrcode, uuid
token = str(uuid.uuid4())
print("トークン:", token)
url = f"http://127.0.0.1:8000/qr_login/{token}/"
qrcode.make(url).save("staff_qr.png")
