-- 小说回收站（软删除）、操作记录、小说自动化（定时续写 / 自动续写大纲）

ALTER TABLE `novel` ADD COLUMN `deleted_at` DATETIME NULL;
ALTER TABLE `novel` ADD KEY `idx_deleted_at` (`deleted_at`);

CREATE TABLE `operation_log` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `admin_id` INT UNSIGNED NOT NULL DEFAULT 0,
  `username` VARCHAR(64) NOT NULL DEFAULT '',
  `action` VARCHAR(64) NOT NULL DEFAULT '',
  `summary` VARCHAR(500) NOT NULL DEFAULT '',
  `method` VARCHAR(10) NOT NULL DEFAULT '',
  `path` VARCHAR(255) NOT NULL DEFAULT '',
  `target_type` VARCHAR(32) NOT NULL DEFAULT '',
  `target_id` INT UNSIGNED NOT NULL DEFAULT 0,
  `success` TINYINT NOT NULL DEFAULT 1,
  `message` VARCHAR(255) NOT NULL DEFAULT '',
  `ip` VARCHAR(64) NOT NULL DEFAULT '',
  `created_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  KEY `idx_created` (`created_at`),
  KEY `idx_target` (`target_type`, `target_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `novel_automation` (
  `novel_id` INT UNSIGNED NOT NULL,
  `schedule_enabled` TINYINT NOT NULL DEFAULT 0,
  `schedule_time` CHAR(5) NOT NULL DEFAULT '08:00',
  `chapters_per_run` INT UNSIGNED NOT NULL DEFAULT 1,
  `target_words` INT UNSIGNED NOT NULL DEFAULT 0,
  `last_run_date` DATE NULL,
  `last_run_message` VARCHAR(255) NOT NULL DEFAULT '',
  `auto_extend_outline` TINYINT NOT NULL DEFAULT 0,
  `extend_chapters` INT UNSIGNED NOT NULL DEFAULT 10,
  `updated_at` DATETIME NULL,
  PRIMARY KEY (`novel_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
