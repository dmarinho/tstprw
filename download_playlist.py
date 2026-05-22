#!/usr/bin/env python3
"""
Baixa todas as músicas de uma playlist pública do Spotify.

Como funciona:
    O Spotify não permite baixar áudio diretamente (DRM). Este script lê a
    lista de faixas da playlist via API do Spotify, procura cada faixa no
    YouTube e baixa o áudio em MP3 com yt-dlp, gravando metadados ID3
    (título, artista, álbum, número da faixa e capa).

Requisitos:
    pip install spotipy yt-dlp mutagen requests
    ffmpeg disponível no PATH (necessário para converter para MP3)

Credenciais do Spotify (gratuitas):
    1. Crie um app em https://developer.spotify.com/dashboard
    2. Pegue o Client ID e Client Secret
    3. Exporte como variáveis de ambiente:
         export SPOTIPY_CLIENT_ID="seu_client_id"
         export SPOTIPY_CLIENT_SECRET="seu_client_secret"

Uso:
    python download_playlist.py <URL_OU_ID_DA_PLAYLIST> [-o pasta_saida]

Exemplo:
    python download_playlist.py https://open.spotify.com/playlist/7L5NK3tl9u9aJnr5RIDOY7
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import requests
import spotipy
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3
from spotipy.oauth2 import SpotifyClientCredentials
from yt_dlp import YoutubeDL


PLAYLIST_RE = re.compile(r"playlist[/:]([A-Za-z0-9]+)")


def extract_playlist_id(value: str) -> str:
    match = PLAYLIST_RE.search(value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]+", value):
        return value
    raise ValueError(f"Não consegui extrair o ID da playlist de: {value}")


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip().rstrip(".")[:180]


def get_spotify_client() -> spotipy.Spotify:
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit(
            "Defina SPOTIPY_CLIENT_ID e SPOTIPY_CLIENT_SECRET. "
            "Crie um app em https://developer.spotify.com/dashboard"
        )
    auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    return spotipy.Spotify(auth_manager=auth, requests_timeout=30)


def fetch_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    tracks: list[dict] = []
    results = sp.playlist_items(
        playlist_id,
        additional_types=("track",),
        fields="items(track(name,artists(name),album(name,images),track_number)),next",
    )
    while results:
        for item in results["items"]:
            track = item.get("track")
            if not track or not track.get("name"):
                continue
            tracks.append(track)
        results = sp.next(results) if results.get("next") else None
    return tracks


def download_track(track: dict, output_dir: Path) -> Path | None:
    artists = ", ".join(a["name"] for a in track["artists"])
    title = track["name"]
    query = f"{artists} - {title}"
    filename = sanitize_filename(query) + ".mp3"
    out_path = output_dir / filename

    if out_path.exists():
        print(f"  já existe, pulando: {filename}")
        return out_path

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / (sanitize_filename(query) + ".%(ext)s")),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch1",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"ytsearch1:{query} audio"])
    except Exception as exc:
        print(f"  erro ao baixar '{query}': {exc}")
        return None

    return out_path if out_path.exists() else None


def tag_mp3(path: Path, track: dict) -> None:
    try:
        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        tags.add(TIT2(encoding=3, text=track["name"]))
        tags.add(TPE1(encoding=3, text=", ".join(a["name"] for a in track["artists"])))
        tags.add(TALB(encoding=3, text=track["album"]["name"]))
        if track.get("track_number"):
            tags.add(TRCK(encoding=3, text=str(track["track_number"])))

        images = track["album"].get("images") or []
        if images:
            try:
                cover = requests.get(images[0]["url"], timeout=15).content
                tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=cover,
                    )
                )
            except requests.RequestException:
                pass

        audio.save()
    except Exception as exc:
        print(f"  aviso: não foi possível gravar tags em {path.name}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Baixa uma playlist do Spotify via YouTube.")
    parser.add_argument("playlist", help="URL ou ID da playlist do Spotify")
    parser.add_argument(
        "-o", "--output", default="downloads", help="Pasta de saída (default: downloads)"
    )
    args = parser.parse_args()

    playlist_id = extract_playlist_id(args.playlist)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    sp = get_spotify_client()

    playlist = sp.playlist(playlist_id, fields="name,owner(display_name)")
    print(f"Playlist: {playlist['name']} (por {playlist['owner']['display_name']})")

    tracks = fetch_playlist_tracks(sp, playlist_id)
    print(f"Faixas encontradas: {len(tracks)}\n")

    ok, fail = 0, 0
    for i, track in enumerate(tracks, 1):
        artists = ", ".join(a["name"] for a in track["artists"])
        print(f"[{i}/{len(tracks)}] {artists} - {track['name']}")
        path = download_track(track, output_dir)
        if path:
            tag_mp3(path, track)
            ok += 1
        else:
            fail += 1

    print(f"\nConcluído. Sucesso: {ok}  Falhas: {fail}  Pasta: {output_dir.resolve()}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
