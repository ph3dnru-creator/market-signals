"""Fetch full captions; if unavailable, transcribe downloaded audio with OpenAI."""
import os
import subprocess
import tempfile
from pathlib import Path

from video_analysis import AnalysisError


class QuietLogger:
    def debug(self, message):
        pass
    def warning(self, message):
        pass
    def error(self, message):
        pass


def fetch_transcript(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    import requests
    try:
        class TimedSession(requests.Session):
            def request(self, *args, **kwargs):
                kwargs.setdefault('timeout', 30)
                return super().request(*args, **kwargs)
        captions = YouTubeTranscriptApi(http_client=TimedSession()).fetch(video_id, languages=['ru', 'en'])
        text = '\n'.join(f'[{s.start:.1f}] {s.text}' for s in captions)
        if len(text) < 400:
            raise AnalysisError('Transcript is too short to analyze')
        return {'complete': True, 'transcript': text, 'source': 'youtube_captions',
                'language': captions.language_code, 'auto_generated': captions.is_generated}
    except Exception:
        # Do not log library exceptions: they can contain signed media URLs.
        return transcribe_audio(video_id)


def transcribe_audio(video_id):
    import yt_dlp
    import requests
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key:
        raise AnalysisError('OPENAI_API_KEY is not configured')
    with tempfile.TemporaryDirectory(prefix='capital-audio-') as directory:
        options = {'format': 'bestaudio/best', 'outtmpl': directory + '/audio.%(ext)s',
                   'noplaylist': True, 'quiet': True, 'logger': QuietLogger(),
                   'socket_timeout': 30, 'retries': 1, 'extractor_retries': 1,
                   'match_filter': lambda info, incomplete: 'Video exceeds 3 hours' if info.get('duration', 0) > 10800 else None,
                   'max_filesize': 250 * 1024 * 1024}
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info('https://www.youtube.com/watch?v=' + video_id, download=True)
                media = Path(downloader.prepare_filename(info))
            if not media.is_file():
                raise AnalysisError('Audio download unavailable')
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(media),
                            '-vn', '-ac', '1', '-ar', '16000', '-b:a', '48k',
                            '-f', 'segment', '-segment_time', '600', directory + '/part%03d.mp3'],
                           check=True, timeout=300, capture_output=True)
        except Exception:
            raise AnalysisError('YouTube captions and audio unavailable') from None
        texts = []
        chunks = sorted(Path(directory).glob('part*.mp3'))
        if not chunks:
            raise AnalysisError('No audio chunks')
        for index, chunk in enumerate(chunks):
            if chunk.stat().st_size > 24 * 1024 * 1024:
                raise AnalysisError('Audio chunk exceeds API limit')
            try:
                with chunk.open('rb') as audio:
                    response = requests.post('https://api.openai.com/v1/audio/transcriptions',
                        headers={'Authorization': 'Bearer ' + key},
                        files={'file': (chunk.name, audio, 'audio/mpeg')},
                        data={'model': 'gpt-4o-mini-transcribe', 'language': 'ru'}, timeout=300)
                if response.status_code != 200:
                    raise AnalysisError(f'Transcription API HTTP {response.status_code}')
                text = response.json().get('text', '')
                if not text.strip():
                    raise AnalysisError('Empty audio transcription')
                texts.append(f'[Фрагмент с {index * 10}:00]\n{text}')
            except AnalysisError:
                raise
            except Exception:
                raise AnalysisError('Audio transcription request failed') from None
        text = '\n'.join(texts)
        if len(text) < 400:
            raise AnalysisError('Transcript is too short to analyze')
        return {'complete': True, 'transcript': text, 'source': 'openai_audio',
                'title': info.get('title'), 'duration': info.get('duration')}
