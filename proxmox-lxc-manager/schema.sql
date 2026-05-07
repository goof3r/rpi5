CREATE DATABASE IF NOT EXISTS proxmox_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE proxmox_manager;

CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS proxmox_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    host VARCHAR(255) NOT NULL,
    port INT DEFAULT 8006,
    node VARCHAR(100) NOT NULL,
    token_id VARCHAR(255) NOT NULL,
    token_secret VARCHAR(255) NOT NULL,
    verify_ssl BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lxc_containers (
    id INT PRIMARY KEY AUTO_INCREMENT,
    vmid INT NOT NULL,
    hostname VARCHAR(255) NOT NULL,
    ram_mb INT NOT NULL,
    disk_gb DECIMAL(10,2) NOT NULL,
    cores INT NOT NULL,
    network_bridge VARCHAR(50) NOT NULL,
    template VARCHAR(255) NOT NULL,
    ip_config VARCHAR(100),
    status VARCHAR(50) DEFAULT 'creating',
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

-- Konto admin tworzone automatycznie przez app.py przy pierwszym starcie
