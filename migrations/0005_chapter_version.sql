-- 章节历史版本：重新生成 / 手动编辑 / 恢复 / 删除前，自动保存被覆盖的旧内容

CREATE TABLE `chapter_version` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `novel_id` INT UNSIGNED NOT NULL,
  `chapter_no` INT UNSIGNED NOT NULL,
  `chapter_id` INT UNSIGNED NOT NULL DEFAULT 0,
  `title` VARCHAR(255) NOT NULL DEFAULT '',
  `summary` TEXT NULL,
  `content` LONGTEXT NULL,
  `word_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `source` VARCHAR(32) NOT NULL DEFAULT '',
  `created_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  KEY `idx_novel_chapter` (`novel_id`, `chapter_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
