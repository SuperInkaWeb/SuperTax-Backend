import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders


def _smtp_send(config, msg):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as sv:
        sv.login(config["gmail_user"], config["gmail_pass"])
        sv.sendmail(config["gmail_user"], config["destino"], msg.as_string())


def _adjuntar(msg, ruta):
    with open(ruta, "rb") as f:
        parte = MIMEBase("application", "octet-stream")
        parte.set_payload(f.read())
    encoders.encode_base64(parte)
    parte.add_header("Content-Disposition", f"attachment; filename={os.path.basename(ruta)}")
    msg.attach(parte)


def enviar_individual(config, serie, numero, ruta_xml, ruta_pdf=None):
    msg = MIMEMultipart()
    msg["From"]    = config["gmail_user"]
    msg["To"]      = config["destino"]
    msg["Subject"] = f"Comprobante {serie}-{numero} procesado"
    msg.attach(MIMEText(
        f"Hola,\n\n"
        f"Se proceso el comprobante {serie}-{numero} (RUC: {config['ruc']}).\n\n"
        f"Archivos adjuntos: {'XML + PDF' if ruta_pdf else 'XML'}\n\n"
        f"- Sistema de Automatizacion SUNAT",
        "plain"
    ))
    for ruta in [ruta_xml, ruta_pdf]:
        if ruta:
            _adjuntar(msg, ruta)
    _smtp_send(config, msg)


def enviar_agrupado(config, archivos):
    msg = MIMEMultipart()
    msg["From"]    = config["gmail_user"]
    msg["To"]      = config["destino"]
    msg["Subject"] = f"{len(archivos)} comprobantes procesados | SUNAT"
    lista = "\n".join(f"  - {s}-{n}" for s, n, *_ in archivos)
    msg.attach(MIMEText(
        f"Hola,\n\n"
        f"Se procesaron {len(archivos)} comprobantes electronicos.\n\n"
        f"Comprobantes:\n{lista}\n\n"
        f"- Sistema de Automatizacion SUNAT",
        "plain"
    ))
    for _, __, ruta_xml, ruta_pdf in archivos:
        for ruta in [ruta_xml, ruta_pdf]:
            if ruta:
                _adjuntar(msg, ruta)
    _smtp_send(config, msg)
