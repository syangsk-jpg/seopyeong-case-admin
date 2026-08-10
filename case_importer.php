<?php
/**
 * CLI importer for case_study board (module 140).
 * /usr/local/php84/bin/php www/_import_case_posts_once.php /abs/posts.json /abs/thumb_dir
 */
if (PHP_SAPI !== 'cli') {
	fwrite(STDERR, "CLI only\n");
	exit(1);
}

$jsonPath = $argv[1] ?? '';
$thumbDir = $argv[2] ?? '';
if (!$jsonPath || !is_file($jsonPath)) {
	fwrite(STDERR, "usage: php _import_case_posts_once.php posts.json thumbs_dir\n");
	exit(1);
}

chdir(__DIR__);
require __DIR__ . '/common/autoload.php';
Context::init();

$module_srl = 140;
$member_srl = 587;
$member = MemberModel::getMemberInfoByMemberSrl($member_srl);
if (!$member || !$member->member_srl) {
	fwrite(STDERR, "member not found\n");
	exit(2);
}

// Pretend logged-in admin for any grant checks
$member->is_admin = 'Y';
Context::set('logged_info', $member);
Context::set('is_logged', true);
$GLOBALS['__logged_info__'] = $member;

$posts = json_decode(file_get_contents($jsonPath), true);
if (!is_array($posts)) {
	fwrite(STDERR, "bad json\n");
	exit(3);
}

$oDocumentController = DocumentController::getInstance();
$oFileController = FileController::getInstance();
$results = [];

foreach ($posts as $post) {
	$title = trim((string)($post['title'] ?? ''));
	$content = (string)($post['content'] ?? '');
	$category_srl = (int)($post['category_srl'] ?? 0);
	$case_label = trim((string)($post['case_label'] ?? ''));
	$tags = trim((string)($post['tags'] ?? ''));
	$thumbName = (string)($post['thumb'] ?? '');

	if ($title === '' || $content === '') {
		$results[] = ['status' => 'skip', 'reason' => 'empty'];
		continue;
	}

	$document_srl = getNextSequence();
	$file_srl = 0;
	$thumbPath = ($thumbDir && $thumbName) ? (rtrim($thumbDir, "/\\") . DIRECTORY_SEPARATOR . $thumbName) : '';

	if ($thumbPath && is_file($thumbPath)) {
		$file_info = array(
			'name' => $thumbName,
			'tmp_name' => $thumbPath,
			'size' => filesize($thumbPath),
			'error' => 0,
		);
		$fOut = $oFileController->insertFile($file_info, $module_srl, $document_srl, 0, true);
		if ($fOut->toBool()) {
			$file_srl = (int)$fOut->get('file_srl');
			$file = FileModel::getFile($file_srl);
			if ($file && !empty($file->uploaded_filename)) {
				$rel = preg_replace('@^\./@', '', str_replace('\\', '/', $file->uploaded_filename));
				$url = '/' . ltrim($rel, '/');
				$img = '<p><img src="' . htmlspecialchars($url, ENT_QUOTES, 'UTF-8')
					. '" alt="' . htmlspecialchars($title, ENT_QUOTES, 'UTF-8')
					. '" editor_component="image_link" data-file-srl="' . $file_srl . '" /></p>';
				$content = $img . "\n" . $content;
			}
		} else {
			fwrite(STDERR, "thumb fail: " . $fOut->getMessage() . "\n");
		}
	}

	$obj = new stdClass();
	$obj->document_srl = $document_srl;
	$obj->module_srl = $module_srl;
	$obj->category_srl = $category_srl;
	$obj->member_srl = $member_srl;
	$obj->user_id = $member->user_id;
	$obj->user_name = $member->user_name ?: $member->nick_name;
	$obj->nick_name = $member->nick_name;
	$obj->email_address = isset($member->email_address) ? $member->email_address : '';
	$obj->title = $title;
	$obj->content = $content;
	$obj->tags = $tags;
	$obj->status = 'PUBLIC';
	$obj->comment_status = 'DENY';
	$obj->allow_trackback = 'N';
	$obj->notify_message = 'N';
	$obj->is_notice = 'N';
	$obj->extra_vars1 = '/case_study/' . $document_srl;
	$obj->extra_vars2 = $case_label;
	if ($file_srl) {
		$obj->uploaded_count = 1;
	}

	$output = $oDocumentController->insertDocument($obj, true);
	if (!$output->toBool()) {
		$results[] = array(
			'status' => 'error',
			'title' => $title,
			'message' => $output->getMessage(),
		);
		fwrite(STDERR, "ERROR {$title}: " . $output->getMessage() . "\n");
		continue;
	}

	DocumentController::insertDocumentExtraVar($module_srl, $document_srl, 1, '/case_study/' . $document_srl, 'url');
	DocumentController::insertDocumentExtraVar($module_srl, $document_srl, 2, $case_label, 'case_label');
	if ($file_srl) {
		$oFileController->setFilesValid($document_srl, 'doc');
	}

	$results[] = array(
		'status' => 'ok',
		'document_srl' => $document_srl,
		'title' => $title,
		'url' => '/case_study/' . $document_srl,
		'file_srl' => $file_srl,
	);
	echo "OK {$document_srl} {$title}\n";
}

@file_put_contents(__DIR__ . '/files/_import_case_posts_result.json', json_encode($results, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
echo "DONE " . count($results) . "\n";
