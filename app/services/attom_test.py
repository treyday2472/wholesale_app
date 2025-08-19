import http.client 

conn = http.client.HTTPSConnection("api.gateway.attomdata.com") 

headers = { 
    'accept': "application/json", 
    'apikey': "4695c62d8319101f2e5b87f5b9e1f71a", 
    } 

conn.request("GET", "/propertyapi/v1.0.0/property/detail?address1=4529%20Winona%20Court&address2=Denver%2C%20CO", headers=headers) 

res = conn.getresponse() 
data = res.read() 

print(data.decode("utf-8"))