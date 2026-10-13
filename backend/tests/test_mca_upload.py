"""Browser upload of MCA Company Master CSVs: validation, streaming load, zip support, and that an uploaded row turns
a crawler-found CIN into a local registry candidate. Synthetic rows only; no network, no credits."""
import io
import zipfile

from app.connectors import mca_upload

HEADER = ("CIN,Company Name,Company Registration Date,Company Category,Company Class,Listing Status,Authorized Capital,"
          "Paidup Capital,Company ROC,Company Address,Pin Code,Company State,Company Status,Company Sub Category,"
          "Company Industrial Classification\n")
ROW = ('U72200KA2009PTC049526,TELERAD TECH PRIVATE LIMITED,2009-04-15,Company limited by shares,Private,Unlisted,'
       '1000000.00,100000.00,ROC Bangalore,"12 TEST ROAD,Bangalore,Karnataka,560001-India", ,Karnataka,Active,'
       'Non-government company,Business Services\n')


def _upload(client, name, body):
    return client.post(f"/api/sources/mca/upload?filename={name}", content=body,
                       headers={"content-type": "application/octet-stream"})


def test_csv_upload_loads_and_name_search_finds_company(client):
    r = _upload(client, "karnataka.csv", (HEADER + ROW).encode())
    assert r.status_code == 202, r.text
    job = mca_upload.wait(r.json()["id"])
    assert job["status"] == "done", job
    assert job["results"][0]["new"] == 1
    assert client.get(f"/api/sources/mca/jobs/{job['id']}").json()["status"] == "done"

    out = client.post("/api/intel/investigate", json={"query": "telerad tech pvt ltd"}).json()
    ids = [c["id"] for c in out["candidates"]]
    assert "mca:U72200KA2009PTC049526" in ids

    out = client.post("/api/intel/investigate", json={"query": "U72200KA2009PTC049526"}).json()
    assert out["subject_id"] == "mca:U72200KA2009PTC049526"


def test_zip_upload_is_extracted(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data/Karnataka.csv", HEADER + ROW)
    r = _upload(client, "karnataka.zip", buf.getvalue())
    assert r.status_code == 202, r.text
    assert mca_upload.wait(r.json()["id"])["status"] == "done"


def test_rejects_wrong_files(client):
    assert _upload(client, "notes.txt", b"hello").status_code == 400
    r = _upload(client, "other.csv", b"LEI,Name\nX,Y\n")
    assert r.status_code == 400 and "not an MCA" in r.json()["detail"]
    assert _upload(client, "empty.csv", b"").status_code == 400
    assert _upload(client, "bad.zip", b"not a zip").status_code == 400
