-- 长篇资料库：人物档案、名词表、分段总结（每 N 章一段的前情总结）

CREATE TABLE `character_profile` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `novel_id` INT UNSIGNED NOT NULL,
  `name` VARCHAR(64) NOT NULL,
  `aliases` VARCHAR(255) NOT NULL DEFAULT '',
  `role` VARCHAR(32) NOT NULL DEFAULT '',
  `appearance` TEXT NULL,
  `personality` TEXT NULL,
  `abilities` TEXT NULL,
  `realm` VARCHAR(128) NOT NULL DEFAULT '',
  `realm_history` JSON NULL,
  `relationships` TEXT NULL,
  `status` VARCHAR(255) NOT NULL DEFAULT '',
  `notes` TEXT NULL,
  `first_chapter` INT UNSIGNED NOT NULL DEFAULT 0,
  `last_chapter` INT UNSIGNED NOT NULL DEFAULT 0,
  `locked` TINYINT NOT NULL DEFAULT 0,
  `created_at` DATETIME NULL,
  `updated_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_novel_name` (`novel_id`, `name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `story_term` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `novel_id` INT UNSIGNED NOT NULL,
  `term` VARCHAR(64) NOT NULL,
  `type` VARCHAR(32) NOT NULL DEFAULT '',
  `description` TEXT NULL,
  `first_chapter` INT UNSIGNED NOT NULL DEFAULT 0,
  `locked` TINYINT NOT NULL DEFAULT 0,
  `created_at` DATETIME NULL,
  `updated_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_novel_term` (`novel_id`, `term`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `arc_summary` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `novel_id` INT UNSIGNED NOT NULL,
  `start_no` INT UNSIGNED NOT NULL,
  `end_no` INT UNSIGNED NOT NULL,
  `content` TEXT NULL,
  `created_at` DATETIME NULL,
  `updated_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_novel_start` (`novel_id`, `start_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 长篇默认开启每 10 章自动审校（之前默认关闭）
UPDATE `system_config` SET `value` = '10' WHERE `key` = 'consistency_auto_interval' AND `value` = '0';
