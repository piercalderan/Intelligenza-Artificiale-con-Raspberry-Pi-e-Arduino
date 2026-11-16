from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from google.cloud import texttospeech as tts

c=tts.TextToSpeechClient()
#v=tts.VoiceSelectionParams(language_code='it-IT', name='it-IT-Wavenet-E')
#v=tts.VoiceSelectionParams(language_code='it-IT', name='it-IT-Neural2-A')
v=tts.VoiceSelectionParams(language_code='it-IT', name='it-IT-Neural2-E')

a=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3)
 
r=c.synthesize_speech(input=tts.SynthesisInput(
    text='Prova voce femminile italiana.'), voice=v, audio_config=a);

open('/tmp/test.mp3','wb').write(r.audio_content)
