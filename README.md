# GEE Web App

A browser-based tool for downloading satellite data (precipitation, temperature, vegetation, land cover, and more) from Google Earth Engine for any area you choose.

## New Feature!

Introducing new product **Dynamic World V1 - Border Zones**. In regional studies, due to finer spatial granularity, administrative effort in tracking mobility or connection is oftentimes completely absent. Dynamic World V1 - Border Zones computes a naive connectivity index based off of pixels identified along the border of two neighboring polygons based off of Dynamic World V1's labelling of each pixel as a specific land use purpose (built-up, grasslands, bodies of water). The index serves only as a proxy of estimation in connectivity, and are subject to the same biases ingrained in the original dataset itself.

## Getting the app

If you are on this GitHub page, click the green **Code** button near the top right, then select **Download ZIP**. Once downloaded, extract the ZIP to a folder you are happy with — then follow the instructions below.

---


# GEE Web App（繁體中文說明）

簡單從 Google Earth Engine 下載您所選區域的衛星資料（降水量、氣溫、植被、土地覆蓋等）。

## 取得應用程式

若您正在瀏覽此 GitHub 頁面，請點擊右上角的綠色 **Code** 按鈕，選擇 **Download ZIP**。下載完成後，將 ZIP 解壓縮至您想要的資料夾——然後按照以下說明操作即可。

---

## What do I need?

You need two things:

**1. A way to run the app — choose one:**

| Option | Best for | Requirement | Windows | Linux |
|--------|----------|-------------|---------|-------|
| **Docker** | Complete environment isolation — nothing is installed on your machine | Install [Docker Desktop](https://www.docker.com/products/docker-desktop) | Not yet available | Supported |
| **Pixi** | Environments where Docker is blocked or unavailable (e.g. managed corporate machines) | Installed automatically if missing (see below) | Supported | Supported |

Both options give the same app in your browser. Docker runs everything in isolated containers so nothing touches your system; Pixi installs the app's dependencies directly on your machine without containers.

> **Docker on Windows:** Docker support is not yet available on Windows. Windows users should use the Pixi option.

> **Pixi installation:** if Pixi is not already installed, the start script will detect this and offer to install it automatically on both Windows and Linux. No manual steps needed.

**2. A Google Earth Engine key file**
A small file (ending in `.json`) that gives the app access to Google Earth Engine. If you do not have one yet, see [Getting a key](#getting-a-key) below.

---

## Starting and stopping the app

Read the block that matches your chosen method.

### Using Docker (Linux only)

> Docker is not yet available on Windows. Windows users should use Pixi below.

```
./docker.sh start
./docker.sh stop
```

### Using Pixi (Windows and Linux)

| Action | Windows | Linux |
|--------|---------|-------|
| Start | Double-click `pixi.bat` → choose **start** | `./pixi.sh start` |
| Stop | Double-click `pixi.bat` → choose **stop** | `./pixi.sh stop` |

The first launch takes a few minutes to set up. You can use this time to read the [User Manual](USER_MANUAL.md). When it is ready, your browser will open automatically.

Closing the browser tab does **not** stop the app — any running download will continue in the background until you use the stop command.

---

## Getting a key

1. Go to [Google Cloud Console](https://console.cloud.google.com/iam-admin/serviceaccounts) and select your project.
2. Open or create a service account that has the **Earth Engine** role.
3. Click **Keys → Add Key → Create new key → JSON** and download the file.

The first time you open the app it will ask you to upload this file. After that it remembers it and you will not be asked again.

---

## 壓縮檔下載好了嗎?

您需要確認兩樣東西：

**1. 執行應用程式的方式——請選擇其一：**

| 選項 | 適合對象 | 需求 | Windows | Linux |
|------|----------|------|---------|-------|
| **Docker** | 需要完整環境隔離——不在本機安裝任何東西 | 安裝 [Docker Desktop](https://www.docker.com/products/docker-desktop) | 尚未支援 | 支援 |
| **Pixi** | Docker 受限或無法使用的環境（例如企業管理電腦） | 若尚未安裝，啟動腳本會自動處理（見下方說明） | 支援 | 支援 |

兩種選項都會在瀏覽器中呈現相同的應用程式。Docker 使用隔離容器執行，不影響系統；Pixi 則直接在您的電腦上安裝依賴套件，不需要容器。

> **Windows 上的 Docker：** Docker 目前尚未支援 Windows。Windows 使用者請使用 Pixi 選項。

> **Pixi 安裝說明：** 若尚未安裝 Pixi，啟動腳本在 Windows 與 Linux 上都會自動偵測並提示您安裝，無需手動操作。

**2. Google Earth Engine 金鑰檔案**
一個小檔案（副檔名為 `.json`），用於授權應用程式存取 Google Earth Engine。如果您還沒有，請參閱下方的[取得金鑰](#取得金鑰)說明。

---

## 啟動與停止應用程式

依您選擇的執行方式，閱讀對應的區塊即可。

### 使用 Docker（僅限 Linux）

> Docker 目前尚未支援 Windows。Windows 使用者請使用下方的 Pixi 區塊。

```
./docker.sh start
./docker.sh stop
```

### 使用 Pixi（Windows 與 Linux）

| 操作 | Windows | Linux |
|------|---------|-------|
| 啟動 | 雙擊 `pixi.bat`，選擇 **start** | `./pixi.sh start` |
| 停止 | 雙擊 `pixi.bat`，選擇 **stop** | `./pixi.sh stop` |

首次啟動需要幾分鐘進行初始化。您可以利用這段時間閱讀[使用手冊](USER_MANUAL_ZH.md)。準備就緒後，瀏覽器將自動開啟。

關閉瀏覽器分頁不會停止應用程式——任何正在進行的下載都會在背景繼續，直到您使用停止指令為止。

---

## 取得金鑰

1. 前往 [Google Cloud Console](https://console.cloud.google.com/iam-admin/serviceaccounts) 並選擇您的專案。
2. 開啟或建立一個具有 **Earth Engine** 角色的服務帳戶。
3. 點擊**金鑰 → 新增金鑰 → 建立新金鑰 → JSON**，然後下載該檔案。

首次開啟應用程式時，系統會要求您上傳此檔案。之後應用程式會記住它，不會再次詢問。