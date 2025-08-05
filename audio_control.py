import subprocess

# Reproduce el archivo con volumen al 30% (0.3)
subprocess.run([
    "ffplay",
    "-nodisp",
    "-autoexit",
    "-af", "volume=0.05",
    r"C:\LivekitAgent\ruido_fondo.m4a"
])
