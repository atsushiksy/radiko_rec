# radiko_rec
# Radiko Time-Free Downloader (Python/Tkinter)

## 概要

本プログラムは、Linux環境においてRadikoのタイムフリー番組を効率的かつ高速にダウンロードし、高互換性のM4Aファイルとして保存するためのグラフィカルユーザーインターフェース（GUI）アプリケーションです。Python 3.xをベースに開発され、標準ライブラリであるTkinterを採用することで、環境依存性を最小限に抑えつつ、直感的な番組選択インターフェースを提供します。

基盤となる認証・ストリーム処理ロジックは、既存の堅牢なシェルスクリプトプロジェクト（例：`rec_radiko_ts` [1]）の知見に基づいてPythonで完全に再構築されています。

## 特徴

  * **Tkinter GUI:** 放送局、日付、番組を視覚的に選択できるユーザーフレンドリーなインターフェース。
  * **高速ダウンロード:** FFmpegのストリームコピー機能（`-acodec copy`）を利用することで、オーディオの再エンコードを回避し、ダウンロード処理時間を大幅に短縮します [1]。
  * **高互換性M4A出力:** FFmpegの`-bsf:a aac_adtstoasc`フィルターを適用することで、生成されるM4Aファイル（AACコーデック）が一般的なメディアプレイヤー（iTunes、iOSなど）で安定して再生されることを保証します [1]。
  * **Radiko Premium対応:** プレミアム会員向けのメールアドレスとパスワードによるログイン機能に対応しており、エリアフリーの番組録音（radiko.jpプレミアム）が可能です [1]。
  * **非同期処理:** 認証やダウンロードといった時間のかかるI/O処理はバックグラウンドスレッドで実行されるため、GUIの応答性を維持します。

## 動作環境

本プログラムは主にLinux環境を想定して設計されています。

### 必須要件

1.  **Python 3.x:** アプリケーションの実行環境。
2.  **FFmpeg:** ストリームキャプチャとファイルコンテナ変換に使用します。バージョン3.x以降が必要であり、HLSおよびAACデコードに対応している必要があります [1]。
3.  **Requestsライブラリ:** Radiko APIへの認証リクエスト（Auth1, Auth2, Premiumログイン）を行うために必須です。

### 依存関係のインストール

Pythonライブラリは`pip`でインストールします。

```bash
pip install requests
```

FFmpegは、お使いのLinuxディストリビューションのパッケージマネージャを使用してインストールしてください（例：Debian/Ubuntuの場合）。

```bash
sudo apt update
sudo apt install ffmpeg
```

## 使用方法

### 1\. 実行

Pythonスクリプトを起動します。

```bash
python3 radiko_downloader_gui.py
```

### 2\. 認証

1.  Radiko Premium会員の場合、メールアドレスとパスワードを入力します。非プレミアムユーザーは空欄のままで構いません。
2.  「**認証 & 局リスト取得**」ボタンを押下します。
3.  認証に成功すると、トークンが取得され、エリアに基づいた放送局リストがドロップダウンメニューにロードされます。
4.  プログラムと同じディレクトリにlogin.yamlファイルを用意し、認証情報を保存することも可能です。ファイルのフォーマットは以下の通りです。

```yaml
mail: foo@sample.com
password: passme
```

### 3\. 番組の選択とダウンロード

1.  ドロップダウンメニューから録音したい**放送局**を選択します。
2.  **日付**（`YYYYMMDD`形式）を入力し、「**番組表ロード**」ボタンを押下します。
3.  リストボックスに選択した局と日付のタイムフリー番組一覧が表示されます。
4.  リストから録音したい**番組**を選択します。
5.  「保存先」を指定し、「**ダウンロード開始**」ボタンを押下します。

ダウンロードが開始されると、バックエンドでFFmpegプロセスが起動し、指定された保存先に高速なストリームコピーによるM4Aファイルが生成されます。進捗バーでダウンロードの進行状況を確認できます。ダウンロード中に「**中断**」ボタンを押すことで、FFmpegプロセスを安全に終了させることが可能です。

## 技術的詳細（開発者向け）

### 参考コード
本コードは、以下のrepoを参考に、AIを駆使して作成されました。このREADME.mdも大部分がAIによって生成されています。

GitHub - uru2/rec_radiko_ts: Radiko timefree program recorder, accessed November 22, 2025, https://github.com/uru2/rec_radiko_ts
inkch/radiko-api: api wrapper for Radiko's programs - GitHub, accessed November 22, 2025, https://github.com/inkch/radiko-api

### 認証ロジックの再現

本プログラムは、シェルスクリプトが行っていた複雑な認証手順をPythonネイティブに再現しています。

  * **PartialKey生成:** 静的な秘密鍵とAuth1で取得したオフセット/長さ情報に基づき、Pythonの`bytes`型スライス操作と`base64`モジュールを用いてPartialKeyを生成します。これにより、外部コマンド（`dd`や`base64`）への依存を排除し、移植性を確保しています [1]。
  * **セッション管理:** Premiumログイン成功時に取得される`radiko_session`を保持し、ログアウト処理（`logout()`）をアプリケーション終了時やダウンロード完了時に確実に実行します [1]。

### 高速化の仕組み

FFmpegへの入力は、認証トークンがHTTPヘッダとして付与されたM3U8プレイリストURLです。出力オプションとして以下の最適化が適用されています。

| オプション | 目的 |
|---|---|
| `-acodec copy` | ストリームを再エンコードせずにコピーし、CPU負荷を劇的に下げ、処理速度を向上させる [1]。 |
| `-bsf:a aac_adtstoasc` | RadikoストリームのADTSヘッダをMP4/M4A互換のASC形式に変換する。コピーモードでのM4A出力に必須 [1]。 |
| `-loglevel error` | FFmpegの冗長なコンソール出力を抑制し、I/O集中を可能にする [1]。 |

## Mac 上で動かすときの注意点

### 必須環境
  1.  Python 3 + Tkinter が使える環境
  macOS の「システム Python」は古かったり Tk が不安定なことがあるので、できれば **[python.org の公式インストーラ版 Python 3.x]** か **brew install python** で入れた Python を使うと安心です。Tkinter は通常付属しますが、import tkinter でエラーが出たら環境を見直し。
  2.  requests のインストール
```bash
    python3 -m pip install requests
```
  3.  FFmpeg が PATH にあること
```bash
    brew install ffmpeg
```

### ライセンス

  MIT

