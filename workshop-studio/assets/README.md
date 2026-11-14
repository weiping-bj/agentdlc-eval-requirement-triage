# assets/ · Workshop Studio S3 Assets

`adlc-lab.zip` 由 `../../tools/build-ws-bundle.sh` 生成到这里（不提交到 git）。
用 Workshop Studio 的 Assets 上传方式把它放到内容的 assets 位置；模板从
`s3://{{.AssetsBucketName}}/{{.AssetsBucketPrefix}}adlc-lab.zip` 下载它。
