// StereoEffect — из примеров three.js (examples/jsm), лицензия MIT, (c) three.js authors.
// https://github.com/mrdoob/three.js — файл приложен без изменений.
( function () {

	class StereoEffect {

		constructor( renderer ) {

			const _stereo = new THREE.StereoCamera();

			_stereo.aspect = 0.5;
			const size = new THREE.Vector2();

			this.setEyeSeparation = function ( eyeSep ) {

				_stereo.eyeSep = eyeSep;

			};

			this.setSize = function ( width, height ) {

				renderer.setSize( width, height );

			};

			this.render = function ( scene, camera ) {

				scene.updateMatrixWorld();
				if ( camera.parent === null ) camera.updateMatrixWorld();

				_stereo.update( camera );

				renderer.getSize( size );
				if ( renderer.autoClear ) renderer.clear();
				renderer.setScissorTest( true );
				renderer.setScissor( 0, 0, size.width / 2, size.height );
				renderer.setViewport( 0, 0, size.width / 2, size.height );
				renderer.render( scene, _stereo.cameraL );
				renderer.setScissor( size.width / 2, 0, size.width / 2, size.height );
				renderer.setViewport( size.width / 2, 0, size.width / 2, size.height );
				renderer.render( scene, _stereo.cameraR );
				renderer.setScissorTest( false );

			};

		}

	}

	THREE.StereoEffect = StereoEffect;

} )();